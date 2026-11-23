/*
 * Quiescent legacy UFS checkpoints for the SM8250 reference wrapper.
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * The upstream PCI/sysbus transports remain unmigratable. Request pointers,
 * MCQ queues and block contents are deliberately not part of this format.
 * See tb371fc/docs/ufs-migration.md for the shared-storage contract.
 */

static void qcom_ufs_hash_u64(GChecksum *sum, uint64_t value)
{
    uint64_t be = cpu_to_be64(value);

    g_checksum_update(sum, (uint8_t *)&be, sizeof(be));
}

/* Immutable, canonical bytes; never overwrite destination configuration. */
static bool qcom_ufs_migration_config(QcomSm8250UfsState *s, Error **errp)
{
    UfsHc *u = &s->ufs;
    g_autoptr(GChecksum) sum = g_checksum_new(G_CHECKSUM_SHA256);
    const char *serial = u->params.serial;
    gsize size = sizeof(s->migration_config);
    unsigned i;

    qcom_ufs_hash_u64(sum, serial != NULL);
    qcom_ufs_hash_u64(sum, serial ? strlen(serial) : 0);
    if (serial) {
        g_checksum_update(sum, (const uint8_t *)serial, strlen(serial));
    }
    qcom_ufs_hash_u64(sum, u->params.nutrs);
    qcom_ufs_hash_u64(sum, u->params.nutmrs);
    qcom_ufs_hash_u64(sum, u->params.mcq);
    qcom_ufs_hash_u64(sum, u->params.wb_max_size);
    qcom_ufs_hash_u64(sum, u->params.wb_min_size);
    /* These packed descriptors already store multibyte fields in wire order. */
    g_checksum_update(sum, (const uint8_t *)&u->device_desc,
                      sizeof(u->device_desc));
    g_checksum_update(sum, (const uint8_t *)&u->geometry_desc,
                      sizeof(u->geometry_desc));
    for (i = 0; i < UFS_MAX_LUS; i++) {
        UfsLu *lu = u->lus[i];
        BlockBackend *blk;
        int64_t bytes;

        qcom_ufs_hash_u64(sum, lu != NULL);
        if (!lu) {
            continue;
        }
        blk = lu->scsi_dev ? lu->scsi_dev->conf.blk : NULL;
        bytes = blk ? blk_getlength(blk) : -1;
        if (bytes < 0) {
            error_setg(errp, "SM8250 UFS migration: LUN %u backend unavailable",
                       i);
            return false;
        }
        g_checksum_update(sum, (const uint8_t *)&lu->unit_desc,
                          sizeof(lu->unit_desc));
        qcom_ufs_hash_u64(sum, bytes);
        qcom_ufs_hash_u64(sum, !blk_supports_write_perm(blk));
    }
    g_checksum_get_digest(sum, s->migration_config, &size);
    return true;
}

static bool qcom_ufs_quiescent(QcomSm8250UfsState *s, Error **errp)
{
    UfsHc *u = &s->ufs;
    unsigned i;

    if (u->params.mcq || u->reg.config || u->reg.mcqconfig || u->resetting ||
        u->reg.utrldbr || u->reg.utmrldbr) {
        error_setg(errp, "SM8250 UFS migration requires quiescent legacy queues "
                   "(UTRLDBR=0x%x UTMRLDBR=0x%x)",
                   u->reg.utrldbr, u->reg.utmrldbr);
        return false;
    }
    for (i = 0; i < u->params.nutrs; i++) {
        UfsRequest *req = &u->req_list[i];

        if (req->state != UFS_REQUEST_IDLE || req->sreq || req->sg ||
            req->sq || req->data_len) {
            error_setg(errp, "SM8250 UFS migration: request slot %u is not idle",
                       i);
            return false;
        }
    }
    for (i = 0; i < UFS_MAX_LUS; i++) {
        if (u->lus[i] && u->lus[i]->scsi_dev) {
            SCSIDevice *dev = u->lus[i]->scsi_dev;
            bool empty;

            WITH_QEMU_LOCK_GUARD(&dev->requests_lock) {
                empty = QTAILQ_EMPTY(&dev->requests);
            }
            if (!empty) {
                error_setg(errp, "SM8250 UFS migration: LUN %u has SCSI I/O", i);
                return false;
            }
        }
    }
    return true;
}

/* Reject states which could violate the core's arithmetic or timer invariants. */
static bool qcom_ufs_migration_valid(QcomSm8250UfsState *s, Error **errp)
{
    UfsHc *u = &s->ufs;
    UfsWb *wb = &u->wb;
    Attributes *a = &u->attributes;
    unsigned i, j;
    uint32_t linked = R_HCS_DP_MASK | R_HCS_UTRLRDY_MASK | R_HCS_UTMRLRDY_MASK;
    Clock *inputs[] = { s->core_clk, s->iface_clk, s->unipro_clk, s->ref_clk,
        s->phy_aux_clk, s->aggre_clk, s->qref_clk, s->sync_clk[0],
        s->sync_clk[1], s->sync_clk[2] };

    for (i = 0; i < ARRAY_SIZE(inputs); i++) {
        if (inputs[i]->multiplier != 1 || inputs[i]->divider != 1) {
            error_setg(errp, "SM8250 UFS migration: invalid input clock ratio");
            return false;
        }
    }

    if (s->link > LINK_HIBERN8 || s->link < LINK_DOWN ||
        u->link_hibern8 != (s->link == LINK_HIBERN8) ||
        (u->reg.hcs & linked) != (s->link == LINK_DOWN ? 0 : linked) ||
        (s->link != LINK_DOWN && (!(u->reg.hce & 1) ||
                                  s->device_reset_held)) ||
        (s->link == LINK_ACTIVE && !s->phy_ready) ||
        (s->phy_ready && !qcom_ufs_phy_can_start(s)) ||
        timer_pending(s->phy_timer) !=
            (qcom_ufs_phy_can_start(s) && !s->phy_ready) ||
        s->uic_cmd > 0xff ||
        timer_pending(s->uic_timer) != s->uic_busy ||
        (s->uic_busy && (!(u->reg.hce & 1) ||
                         (u->reg.hcs & R_HCS_UCRDY_MASK))) ||
        !timer_pending(&u->idle_timer)) {
        error_setg(errp, "SM8250 UFS migration: inconsistent link or timer state");
        return false;
    }
    if (wb->min_bytes > wb->curr_bytes || wb->curr_bytes > wb->max_bytes ||
        wb->used_bytes > wb->curr_bytes ||
        wb->fifo_max_bytes > wb->max_bytes ||
        wb->fifo_curr_bytes > MIN(wb->fifo_max_bytes, wb->curr_bytes) ||
        wb->pinned_max_bytes > wb->max_bytes ||
        wb->non_pinned_min_bytes > wb->max_bytes - wb->pinned_max_bytes ||
        wb->pinned_curr_bytes > MIN(wb->curr_bytes, wb->pinned_max_bytes) ||
        wb->pinned_used_bytes > MIN(wb->pinned_curr_bytes, wb->used_bytes) ||
        wb->used_bytes - wb->pinned_used_bytes >
            wb->curr_bytes - wb->pinned_curr_bytes ||
        a->wb_buffer_partial_flush_mode >= UFS_WB_FLUSH_MODE_MAX ||
        a->wb_buffer_flush_status >= UFS_WB_FLUSH_STATUS_MAX ||
        a->wb_buffer_resize_hint >= UFS_WB_RESIZE_HINT_MAX ||
        a->wb_buffer_resize_en >= UFS_WB_RESIZE_OP_MAX ||
        a->wb_buffer_resize_status >= UFS_WB_RESIZE_STATUS_MAX ||
        a->hid_state > UFS_HID_STATE_DEFRAG_NOT_REQUIRED ||
        a->defrag_op > UFS_HID_OP_DEFRAG || a->hid_prog_ratio > 100 ||
        u->hid_defrag_remaining > u->hid_defrag_total ||
        u->hid_defrag_remaining > u->hid_fragment_count) {
        error_setg(errp, "SM8250 UFS migration: invalid WB or HID state");
        return false;
    }
    for (i = 0; i < sizeof(u->flags); i++) {
        if (((uint8_t *)&u->flags)[i] > 1) {
            error_setg(errp, "SM8250 UFS migration: invalid flag %u", i);
            return false;
        }
    }
    for (i = 0; i < PHY_WORDS; i++) {
        if (s->phy_regs[i] > 0xff ||
            ((!qcom_ufs_phy_reg(i * 4) || i * 4 == PHY_READY) &&
             (s->phy_regs[i] || s->phy_written[i]))) {
            error_setg(errp, "SM8250 UFS migration: invalid PHY word %u", i);
            return false;
        }
    }
    for (i = 0; i < DME_VALUES; i++) {
        QcomUfsDmeValue *e = &s->dme[i];
        uint32_t initial;

        if (!e->used) {
            continue;
        }
        if (!qcom_ufs_dme_writable(e->id) ||
            qcom_ufs_dme_default(s, e->id, e->selector, &initial) ||
            ((e->id == 0x1560 || e->id == 0x1580) &&
             (!e->value || e->value > 2)) ||
            ((e->id == 0x1568 || e->id == 0x1583) &&
             (!e->value || e->value > 4)) ||
            (e->id == 0x156a && e->value != 1 && e->value != 2) ||
            (e->id == 0x1571 && !qcom_ufs_valid_power_mode(e->value))) {
            error_setg(errp, "SM8250 UFS migration: invalid DME entry %u", i);
            return false;
        }
        for (j = 0; j < i; j++) {
            QcomUfsDmeValue *other = &s->dme[j];

            if (other->used && e->id == other->id &&
                e->selector == other->selector && e->peer == other->peer) {
                error_setg(errp, "SM8250 UFS migration: duplicate DME entry %u",
                           i);
                return false;
            }
        }
    }
    if (s->vendor[V_HW_VERSION / 4] != s->hw_version ||
        s->vendor[0xd0 / 4] != (4 << 4) || s->vendor[0xcc / 4]) {
        error_setg(errp, "SM8250 UFS migration: invalid vendor identity");
        return false;
    }
    return true;
}

static bool qcom_ufs_pre_save(void *opaque, Error **errp)
{
    QcomSm8250UfsState *s = opaque;

    return qcom_ufs_quiescent(s, errp) &&
           qcom_ufs_migration_valid(s, errp) &&
           qcom_ufs_migration_config(s, errp);
}

static bool qcom_ufs_pre_load(void *opaque, Error **errp)
{
    QcomSm8250UfsState *s = opaque;

    s->initialized = false;
    return qcom_ufs_migration_config(s, errp);
}

static bool qcom_ufs_post_load(void *opaque, int version_id, Error **errp)
{
    QcomSm8250UfsState *s = opaque;

    s->initialized = true;
    if (!qcom_ufs_quiescent(s, errp) || !qcom_ufs_migration_valid(s, errp)) {
        s->initialized = false;
        return false;
    }
    /* Request/BH pointers are the new instance's idle allocations. */
    qcom_ufs_symbols(s);
    qcom_ufs_irq(s);
    return true;
}

static const VMStateDescription vmstate_qcom_ufs_reg = {
    .name = "sm8250-ufs/legacy-hci",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(cap, UfsReg),
        VMSTATE_UINT32_EQUAL(mcqcap, UfsReg),
        VMSTATE_UINT32_EQUAL(ver, UfsReg),
        VMSTATE_UINT32(rsvd1, UfsReg),
        VMSTATE_UINT32_EQUAL(hcpid, UfsReg),
        VMSTATE_UINT32_EQUAL(hcmid, UfsReg),
        VMSTATE_UINT32(ahit, UfsReg),
        VMSTATE_UINT32(rsvd2, UfsReg),
        VMSTATE_UINT32(is, UfsReg),
        VMSTATE_UINT32(ie, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd3, UfsReg, 2),
        VMSTATE_UINT32(hcs, UfsReg),
        VMSTATE_UINT32(hce, UfsReg),
        VMSTATE_UINT32(uecpa, UfsReg),
        VMSTATE_UINT32(uecdl, UfsReg),
        VMSTATE_UINT32(uecn, UfsReg),
        VMSTATE_UINT32(uect, UfsReg),
        VMSTATE_UINT32(uecdme, UfsReg),
        VMSTATE_UINT32(utriacr, UfsReg),
        VMSTATE_UINT32(utrlba, UfsReg),
        VMSTATE_UINT32(utrlbau, UfsReg),
        VMSTATE_UINT32(utrldbr, UfsReg),
        VMSTATE_UINT32(utrlclr, UfsReg),
        VMSTATE_UINT32(utrlrsr, UfsReg),
        VMSTATE_UINT32(utrlcnr, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd4, UfsReg, 2),
        VMSTATE_UINT32(utmrlba, UfsReg),
        VMSTATE_UINT32(utmrlbau, UfsReg),
        VMSTATE_UINT32(utmrldbr, UfsReg),
        VMSTATE_UINT32(utmrlclr, UfsReg),
        VMSTATE_UINT32(utmrlrsr, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd5, UfsReg, 3),
        VMSTATE_UINT32(uiccmd, UfsReg),
        VMSTATE_UINT32(ucmdarg1, UfsReg),
        VMSTATE_UINT32(ucmdarg2, UfsReg),
        VMSTATE_UINT32(ucmdarg3, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd6, UfsReg, 4),
        VMSTATE_UINT32_ARRAY(rsvd7, UfsReg, 4),
        VMSTATE_UINT32_ARRAY(rsvd8, UfsReg, 16),
        VMSTATE_UINT32_EQUAL(ccap, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd9, UfsReg, 127),
        VMSTATE_UINT32(config, UfsReg),
        VMSTATE_UINT32_ARRAY(rsvd10, UfsReg, 3),
        VMSTATE_UINT32_ARRAY(rsvd11, UfsReg, 28),
        VMSTATE_UINT32(mcqconfig, UfsReg),
        VMSTATE_UINT32(esilba, UfsReg),
        VMSTATE_UINT32(esiuba, UfsReg),
        VMSTATE_END_OF_LIST()
    },
};

/* Attributes are not packed; multibyte values are already big-endian.
 * Preserve each field's bytes, without serializing native padding. */
#define UFS_WIRE_FIELD(f, n) \
    VMSTATE_BUFFER_UNSAFE_INFO_TEST(f, Attributes, NULL, 0, \
                                   vmstate_info_buffer, n)
static const VMStateDescription vmstate_qcom_ufs_attributes = {
    .name = "sm8250-ufs/query-attributes",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT8(boot_lun_en, Attributes),
        VMSTATE_UINT8(reserved, Attributes),
        VMSTATE_UINT8(current_power_mode, Attributes),
        VMSTATE_UINT8(active_icc_level, Attributes),
        VMSTATE_UINT8(out_of_order_data_en, Attributes),
        VMSTATE_UINT8(background_op_status, Attributes),
        VMSTATE_UINT8(purge_status, Attributes),
        VMSTATE_UINT8(max_data_in_size, Attributes),
        VMSTATE_UINT8(max_data_out_size, Attributes),
        UFS_WIRE_FIELD(dyn_cap_needed, 4),
        VMSTATE_UINT8(ref_clk_freq, Attributes),
        VMSTATE_UINT8(config_descr_lock, Attributes),
        VMSTATE_UINT8(max_num_of_rtt, Attributes),
        UFS_WIRE_FIELD(exception_event_control, 2),
        UFS_WIRE_FIELD(exception_event_status, 2),
        UFS_WIRE_FIELD(seconds_passed, 4),
        UFS_WIRE_FIELD(context_conf, 2),
        VMSTATE_UINT8(obsolete, Attributes),
        VMSTATE_UINT8_ARRAY(reserved2, Attributes, 2),
        VMSTATE_UINT8(device_ffu_status, Attributes),
        VMSTATE_UINT8(psa_state, Attributes),
        UFS_WIRE_FIELD(psa_data_size, 4),
        VMSTATE_UINT8(ref_clk_gating_wait_time, Attributes),
        VMSTATE_UINT8(device_case_rough_temperature, Attributes),
        VMSTATE_UINT8(device_too_high_temp_boundary, Attributes),
        VMSTATE_UINT8(device_too_low_temp_boundary, Attributes),
        VMSTATE_UINT8(throttling_status, Attributes),
        VMSTATE_UINT8(wb_buffer_flush_status, Attributes),
        VMSTATE_UINT8(available_wb_buffer_size, Attributes),
        VMSTATE_UINT8(wb_buffer_life_time_est, Attributes),
        UFS_WIRE_FIELD(current_wb_buffer_size, 4),
        VMSTATE_UINT8_ARRAY(reserved3, Attributes, 10),
        VMSTATE_UINT8(ext_iid_en, Attributes),
        UFS_WIRE_FIELD(host_hint_cache_size, 2),
        VMSTATE_UINT8(refresh_status, Attributes),
        VMSTATE_UINT8(refresh_freq, Attributes),
        VMSTATE_UINT8(refresh_unit, Attributes),
        VMSTATE_UINT8(refresh_method, Attributes),
        UFS_WIRE_FIELD(timestamp, 8),
        VMSTATE_UINT8_ARRAY(reserved4, Attributes, 3),
        UFS_WIRE_FIELD(device_level_exception_id, 8),
        VMSTATE_UINT8(defrag_op, Attributes),
        UFS_WIRE_FIELD(hid_avail_size, 4),
        UFS_WIRE_FIELD(hid_size, 4),
        VMSTATE_UINT8(hid_prog_ratio, Attributes),
        VMSTATE_UINT8(hid_state, Attributes),
        VMSTATE_UINT8_ARRAY(reserved5, Attributes, 2),
        VMSTATE_UINT8(wb_buffer_resize_hint, Attributes),
        VMSTATE_UINT8(wb_buffer_resize_en, Attributes),
        VMSTATE_UINT8(wb_buffer_resize_status, Attributes),
        VMSTATE_UINT8(wb_buffer_partial_flush_mode, Attributes),
        UFS_WIRE_FIELD(max_fifo_wb_partial_flush_mode, 4),
        UFS_WIRE_FIELD(curr_fifo_wb_partial_flush_mode, 4),
        UFS_WIRE_FIELD(pinned_wb_buffer_curr_alloc_units, 4),
        VMSTATE_UINT8(pinned_wb_buffer_avail_percent, Attributes),
        UFS_WIRE_FIELD(pinned_wb_cumm_written_size, 4),
        UFS_WIRE_FIELD(pinned_wb_num_alloc_units, 4),
        UFS_WIRE_FIELD(non_pinned_wb_min_num_alloc_units, 4),
        VMSTATE_UINT8_ARRAY(reserved6, Attributes, 184),
        VMSTATE_END_OF_LIST()
    },
};
#undef UFS_WIRE_FIELD

static const VMStateDescription vmstate_qcom_ufs_wb = {
    .name = "sm8250-ufs/write-booster",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT64_EQUAL(max_bytes, UfsWb),
        VMSTATE_UINT64_EQUAL(min_bytes, UfsWb),
        VMSTATE_UINT64(curr_bytes, UfsWb),
        VMSTATE_UINT64(used_bytes, UfsWb),
        VMSTATE_UINT64_EQUAL(resize_bytes, UfsWb),
        VMSTATE_UINT64(fifo_max_bytes, UfsWb),
        VMSTATE_UINT64(fifo_curr_bytes, UfsWb),
        VMSTATE_UINT64(pinned_max_bytes, UfsWb),
        VMSTATE_UINT64(non_pinned_min_bytes, UfsWb),
        VMSTATE_UINT64(pinned_curr_bytes, UfsWb),
        VMSTATE_UINT64(pinned_used_bytes, UfsWb),
        VMSTATE_UINT64(pinned_total_written_bytes, UfsWb),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qcom_ufs_core = {
    .name = "sm8250-ufs/quiescent-core",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_STRUCT(reg, UfsHc, 0, vmstate_qcom_ufs_reg, UfsReg),
        VMSTATE_STRUCT(attributes, UfsHc, 0, vmstate_qcom_ufs_attributes,
                       Attributes),
        VMSTATE_BUFFER_UNSAFE_INFO_TEST(flags, UfsHc, NULL, 0,
                                       vmstate_info_buffer, sizeof(Flags)),
        VMSTATE_STRUCT(wb, UfsHc, 0, vmstate_qcom_ufs_wb, UfsWb),
        VMSTATE_BOOL(link_hibern8, UfsHc),
        VMSTATE_UINT8(temperature, UfsHc),
        VMSTATE_TIMER(idle_timer, UfsHc),
        VMSTATE_UINT32(hid_fragment_count, UfsHc),
        VMSTATE_UINT32(hid_defrag_total, UfsHc),
        VMSTATE_UINT32(hid_defrag_remaining, UfsHc),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qcom_ufs_dme = {
    .name = "sm8250-ufs/dme-value",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT16(id, QcomUfsDmeValue),
        VMSTATE_UINT16(selector, QcomUfsDmeValue),
        VMSTATE_UINT32(value, QcomUfsDmeValue),
        VMSTATE_BOOL(peer, QcomUfsDmeValue),
        VMSTATE_BOOL(used, QcomUfsDmeValue),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qcom_ufs = {
    .name = TYPE_QCOM_SM8250_UFS,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save_errp = qcom_ufs_pre_save,
    .pre_load_errp = qcom_ufs_pre_load,
    .post_load_errp = qcom_ufs_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(hw_version, QcomSm8250UfsState),
        VMSTATE_UINT32_EQUAL(hci_version, QcomSm8250UfsState),
        VMSTATE_UINT32_EQUAL(symbol_hz, QcomSm8250UfsState),
        VMSTATE_ARRAY(migration_config, QcomSm8250UfsState, 32, 0,
                      vmstate_info_uint8_equal, uint8_t),
        VMSTATE_STRUCT(ufs, QcomSm8250UfsState, 0, vmstate_qcom_ufs_core,
                       UfsHc),
        VMSTATE_UINT32_ARRAY(vendor, QcomSm8250UfsState,
                             QCOM_SM8250_UFS_HCI_SIZE / 4),
        VMSTATE_UINT32_ARRAY(phy_regs, QcomSm8250UfsState, PHY_WORDS),
        VMSTATE_BOOL_ARRAY(phy_written, QcomSm8250UfsState, PHY_WORDS),
        VMSTATE_STRUCT_ARRAY(dme, QcomSm8250UfsState, DME_VALUES, 0,
                             vmstate_qcom_ufs_dme, QcomUfsDmeValue),
        VMSTATE_UINT32(uic_cmd, QcomSm8250UfsState),
        VMSTATE_UINT32_ARRAY(uic_args, QcomSm8250UfsState, 3),
        VMSTATE_UINT32(link, QcomSm8250UfsState),
        VMSTATE_BOOL(power_on, QcomSm8250UfsState),
        VMSTATE_BOOL(reset_held, QcomSm8250UfsState),
        VMSTATE_BOOL(device_reset_held, QcomSm8250UfsState),
        VMSTATE_BOOL(phy_ready, QcomSm8250UfsState),
        VMSTATE_BOOL(uic_busy, QcomSm8250UfsState),
        VMSTATE_CLOCK(core_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(iface_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(unipro_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(ref_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(phy_aux_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(aggre_clk, QcomSm8250UfsState),
        VMSTATE_CLOCK(qref_clk, QcomSm8250UfsState),
        VMSTATE_ARRAY_CLOCK(sync_clk, QcomSm8250UfsState, 3),
        VMSTATE_TIMER_PTR(phy_timer, QcomSm8250UfsState),
        VMSTATE_TIMER_PTR(uic_timer, QcomSm8250UfsState),
        VMSTATE_END_OF_LIST()
    },
};
