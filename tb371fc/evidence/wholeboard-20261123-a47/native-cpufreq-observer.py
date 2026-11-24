#!/usr/bin/env python3
# Private bounded stopped diagnostic extension; never modifies guest state.
import json
import diagnose_kernel
from observe_aop import capture_aop_peer
NOC_PORTS=json.loads("[{\"path\":\"/machine/noc-qos-system-0\",\"fabric\":\"system\",\"port\":0,\"physical_port_address\":23273472,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-pimem\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":40,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-system-1\",\"fabric\":\"system\",\"port\":1,\"physical_port_address\":23277568,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-gic\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":40,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-0\",\"fabric\":\"aggre1\",\"port\":0,\"physical_port_address\":23994368,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-usb3-0\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-1\",\"fabric\":\"aggre1\",\"port\":1,\"physical_port_address\":23998464,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-usb3-1\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-2\",\"fabric\":\"aggre1\",\"port\":2,\"physical_port_address\":24002560,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-sdc4\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-3\",\"fabric\":\"aggre1\",\"port\":3,\"physical_port_address\":24006656,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-ufs-mem\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-4\",\"fabric\":\"aggre1\",\"port\":4,\"physical_port_address\":24010752,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-pcie3-modem\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-5\",\"fabric\":\"aggre1\",\"port\":5,\"physical_port_address\":24014848,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-qup1\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-6\",\"fabric\":\"aggre1\",\"port\":6,\"physical_port_address\":24018944,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-qup2\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-7\",\"fabric\":\"aggre1\",\"port\":7,\"physical_port_address\":24023040,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-qspi\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre1-8\",\"fabric\":\"aggre1\",\"port\":8,\"physical_port_address\":24027136,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-tsif\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-0\",\"fabric\":\"aggre2\",\"port\":0,\"physical_port_address\":24129536,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-cnoc\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":40,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-1\",\"fabric\":\"aggre2\",\"port\":1,\"physical_port_address\":24133632,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-crypto\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":40,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-2\",\"fabric\":\"aggre2\",\"port\":2,\"physical_port_address\":24137728,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-ipa\",\"defer_init_qos\":true,\"source_correlated_dt_words\":{\"mainctl\":40,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-3\",\"fabric\":\"aggre2\",\"port\":3,\"physical_port_address\":24141824,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-sdc2\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-4\",\"fabric\":\"aggre2\",\"port\":4,\"physical_port_address\":24145920,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-ufs-card\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-7\",\"fabric\":\"aggre2\",\"port\":7,\"physical_port_address\":24158208,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-qdss-etr\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-8\",\"fabric\":\"aggre2\",\"port\":8,\"physical_port_address\":24162304,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-pcie3-0\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-9\",\"fabric\":\"aggre2\",\"port\":9,\"physical_port_address\":24166400,\"dt_client\":\"/soc/ad-hoc-bus/mas-xm-pcie3-1\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-11\",\"fabric\":\"aggre2\",\"port\":11,\"physical_port_address\":24174592,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-qdss-bam\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-aggre2-12\",\"fabric\":\"aggre2\",\"port\":12,\"physical_port_address\":24178688,\"dt_client\":\"/soc/ad-hoc-bus/mas-qhm-qup0\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":32,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-compute-6\",\"fabric\":\"compute\",\"port\":6,\"physical_port_address\":24338432,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-npu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-compute-7\",\"fabric\":\"compute\",\"port\":7,\"physical_port_address\":24340480,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-npu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-0\",\"fabric\":\"mmss\",\"port\":0,\"physical_port_address\":24420352,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-camnoc-sf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-1\",\"fabric\":\"mmss\",\"port\":1,\"physical_port_address\":24422400,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-camnoc-sf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-2\",\"fabric\":\"mmss\",\"port\":2,\"physical_port_address\":24424448,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-camnoc-icp\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":88,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-4\",\"fabric\":\"mmss\",\"port\":4,\"physical_port_address\":24428544,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-camnoc-hf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-5\",\"fabric\":\"mmss\",\"port\":5,\"physical_port_address\":24430592,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-camnoc-hf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-6\",\"fabric\":\"mmss\",\"port\":6,\"physical_port_address\":24432640,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-mdp0\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-8\",\"fabric\":\"mmss\",\"port\":8,\"physical_port_address\":24436736,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-mdp1\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-10\",\"fabric\":\"mmss\",\"port\":10,\"physical_port_address\":24440832,\"dt_client\":\"/soc/ad-hoc-bus/mas-qxm-rot\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-12\",\"fabric\":\"mmss\",\"port\":12,\"physical_port_address\":24444928,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-video0\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-13\",\"fabric\":\"mmss\",\"port\":13,\"physical_port_address\":24446976,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-video1\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-mmss-14\",\"fabric\":\"mmss\",\"port\":14,\"physical_port_address\":24449024,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-video-cvp\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-0\",\"fabric\":\"gem\",\"port\":0,\"physical_port_address\":152178688,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-cmpnoc\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-1\",\"fabric\":\"gem\",\"port\":1,\"physical_port_address\":152182784,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-gpu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-2\",\"fabric\":\"gem\",\"port\":2,\"physical_port_address\":152186880,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-mnoc-hf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-3\",\"fabric\":\"gem\",\"port\":3,\"physical_port_address\":152190976,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-mnoc-sf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-64\",\"fabric\":\"gem\",\"port\":64,\"physical_port_address\":152440832,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-cmpnoc\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-65\",\"fabric\":\"gem\",\"port\":65,\"physical_port_address\":152444928,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-gpu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-66\",\"fabric\":\"gem\",\"port\":66,\"physical_port_address\":152449024,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-mnoc-hf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-67\",\"fabric\":\"gem\",\"port\":67,\"physical_port_address\":152453120,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-mnoc-sf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-127\",\"fabric\":\"gem\",\"port\":127,\"physical_port_address\":152698880,\"dt_client\":\"/soc/ad-hoc-bus/mas-alm-gpu-tcu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":16,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-128\",\"fabric\":\"gem\",\"port\":128,\"physical_port_address\":152702976,\"dt_client\":\"/soc/ad-hoc-bus/mas-alm-sys-tcu\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":96,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-129\",\"fabric\":\"gem\",\"port\":129,\"physical_port_address\":152707072,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-pcie\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-130\",\"fabric\":\"gem\",\"port\":130,\"physical_port_address\":152711168,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-snoc-gc\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}},{\"path\":\"/machine/noc-qos-gem-131\",\"fabric\":\"gem\",\"port\":131,\"physical_port_address\":152715264,\"dt_client\":\"/soc/ad-hoc-bus/mas-qnm-snoc-sf\",\"defer_init_qos\":false,\"source_correlated_dt_words\":{\"mainctl\":8,\"limitbw\":0,\"regul0ctl\":0,\"regul0bw\":0}}]")
def capture_noc(command):
    report={"scope":"Stopped raw NoC QoS QOM observations; source-correlated DT comparisons",
            "guest_memory_written":False,"device_mmio_read":False,
            "guest_cpu_executed_by_observer":False,"android_boot_passed":False,
            "dtb_sha256":"5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
            "ports":[]}
    report["stopped_status"]=command("query-status")
    if report["stopped_status"].get("running") is not False:
        raise RuntimeError("NoC observation requires a stopped guest")
    for expected in NOC_PORTS:
        item=dict(expected)
        path=item["path"]
        item["reference_enabled"]=command("qom-get",{"path":path,"property":"enabled-reference"})
        item["observed_physical_port_address"]=command("qom-get",{"path":path,"property":"physical-port-address"})
        item["address_matches"]=item["observed_physical_port_address"]==item["physical_port_address"]
        item["words"]={key:command("qom-get",{"path":path,"property":key})
                       for key in ("mainctl","limitbw","regul0ctl","regul0bw")}
        item["configuration_matches_source_correlated_dt"]=item["words"]==item["source_correlated_dt_words"]
        report["ports"].append(item)
    report["stopped_status_after"]=command("query-status")
    if report["stopped_status_after"].get("running") is not False:
        raise RuntimeError("NoC guest resumed during observation")
    report["port_count"]=len(report["ports"])
    report["address_matches"]=sum(p["address_matches"] for p in report["ports"])
    report["nonzero_mainctl_ports"]=sum(p["words"]["mainctl"]!=0 for p in report["ports"])
    report["dt_matches_excluding_deferred"]=sum(p["configuration_matches_source_correlated_dt"] for p in report["ports"] if not p["defer_init_qos"])
    report["nondeferred_port_count"]=sum(not p["defer_init_qos"] for p in report["ports"])
    report["interpretation"]="Raw configuration only. A matching nonzero word demonstrates stored guest programming; it does not establish traffic, bandwidth enforcement, SBM, power readiness or full boot. Deferred IPA need not match its eventual DT policy."
    return report
SMMUV500_TBUS=json.loads("[{\"dt_path\":\"/soc/apps-smmu@15000000/anoc_1_tbu@15185000\",\"index\":0,\"base\":353914880,\"status_address\":353903104,\"sid_start\":0,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/anoc_2_tbu@15189000\",\"index\":1,\"base\":353931264,\"status_address\":353903112,\"sid_start\":1024,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/mnoc_hf_0_tbu@1518d000\",\"index\":2,\"base\":353947648,\"status_address\":353903120,\"sid_start\":2048,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/mnoc_hf_1_tbu@15191000\",\"index\":3,\"base\":353964032,\"status_address\":353903128,\"sid_start\":3072,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/compute_dsp_1_tbu@15195000\",\"index\":4,\"base\":353980416,\"status_address\":353903136,\"sid_start\":4096,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/compute_dsp_0_tbu@15199000\",\"index\":5,\"base\":353996800,\"status_address\":353903144,\"sid_start\":5120,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/adsp_tbu@1519d000\",\"index\":6,\"base\":354013184,\"status_address\":353903152,\"sid_start\":6144,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/anoc_1_pcie_tbu@151a1000\",\"index\":7,\"base\":354029568,\"status_address\":353903160,\"sid_start\":7168,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/mnoc_sf_0_tbu@151a5000\",\"index\":8,\"base\":354045952,\"status_address\":353903168,\"sid_start\":8192,\"sid_count\":1024},{\"dt_path\":\"/soc/apps-smmu@15000000/mnoc_sf_1_tbu@151a9000\",\"index\":9,\"base\":354062336,\"status_address\":353903176,\"sid_start\":9216,\"sid_count\":1024}]")
def capture_apps_smmuv500(command):
    path="/machine/apps-smmu-v500"
    report={"scope":"Stopped raw apps SMMUv500 QOM observation only",
            "guest_memory_written":False,"device_mmio_read":False,
            "guest_cpu_executed_by_observer":False,
            "native_dma_exercised_by_observer":False,
            "android_boot_passed":False,
            "dtb_sha256":"5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"}
    report["stopped_status"]=command("query-status")
    if report["stopped_status"].get("running") is not False:
        raise RuntimeError("SMMUv500 observation requires a stopped guest")
    report["configuration"]={key:command("qom-get",{"path":path,"property":key})
        for key in ("enabled-reference","reference-hw-version","tcu-base","tbu-count")}
    report["state"]={key:command("qom-get",{"path":path,"property":key})
        for key in ("ecats-owner","dma-blocked-mask")}
    report["tbus"]=[]
    properties=("base","status-address","sid-start","halt-sid","va","trigger",
                "axuser","par","status","halt-pending","halted","ecats-pending",
                "halt-deadline-ns","ecats-deadline-ns","captured-sid",
                "captured-va","captured-write")
    for expected in SMMUV500_TBUS:
        item=dict(expected)
        index=item["index"]
        item["observed"]={key:command("qom-get",{"path":path,"property":f"tbu{index}-{key}"})
                          for key in properties}
        observed=item["observed"]
        item["geometry_matches_actual_dt"]=(observed["base"]==item["base"]
            and observed["status-address"]==item["status_address"]
            and observed["sid-start"]==item["sid_start"])
        report["tbus"].append(item)
    report["stopped_status_after"]=command("query-status")
    if report["stopped_status_after"].get("running") is not False:
        raise RuntimeError("SMMUv500 guest resumed during observation")
    report["tbu_count"]=len(report["tbus"])
    report["geometry_matches"]=sum(x["geometry_matches_actual_dt"] for x in report["tbus"])
    report["interpretation"]="Reference model identity and raw device state only. Zero requests do not prove a native TBU probe or DMA test; native progress requires separate kernel-log evidence. Version and delays are declared reference assumptions, and firmware stream/context handoff remains unknown."
    return report
TSENS_CONTROLLERS=json.loads("[{\"path\":\"/machine/tsens0\",\"controller_id\":0,\"dt_path\":\"/soc/tsens@c222000\",\"srot_base\":203563008,\"tm_base\":203829248,\"dt_srot_size\":4,\"dt_tm_size\":511,\"upper_lower_spi\":506,\"critical_spi\":508,\"thermal_reference_sensor_ids\":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]},{\"path\":\"/machine/tsens1\",\"controller_id\":1,\"dt_path\":\"/soc/tsens@c223000\",\"srot_base\":203567104,\"tm_base\":203837440,\"dt_srot_size\":4,\"dt_tm_size\":511,\"upper_lower_spi\":507,\"critical_spi\":509,\"thermal_reference_sensor_ids\":[0,1,2,3,4,5,6,7,8]}]")
def capture_tsens24xx(command):
    report={"scope":"Stopped raw TSENS24xx QOM state and explicit reference inputs",
            "guest_memory_written":False,"device_mmio_read":False,
            "host_environment_modified_by_observer":False,
            "guest_cpu_executed_by_observer":False,
            "analog_calibration_observed":False,"android_boot_passed":False,
            "dtb_sha256":"5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
            "controllers":[]}
    report["stopped_status"]=command("query-status")
    if report["stopped_status"].get("running") is not False:
        raise RuntimeError("TSENS observation requires a stopped guest")
    for expected in TSENS_CONTROLLERS:
        item=dict(expected)
        path=item["path"]
        item["configuration"]={key:command("qom-get",{"path":path,"property":key})
            for key in ("enabled-reference","controller-id",
                        "reference-boot-enable-mask",
                        "reference-temperature-millicelsius","srot-base",
                        "tm-base","sensor-count","sampling-period-ns")}
        config=item["configuration"]
        if type(config["enabled-reference"]) is not bool or any(
                type(value) is not int for key,value in config.items()
                if key!="enabled-reference"):
            raise RuntimeError("TSENS configuration has unexpected QOM types")
        item["geometry_matches_actual_dt"]=(config["controller-id"]==item["controller_id"]
            and config["srot-base"]==item["srot_base"]
            and config["tm-base"]==item["tm_base"])
        item["state"]={key:command("qom-get",{"path":path,"property":key})
            for key in ("control","interrupt-enable","upper-lower-status",
                        "upper-lower-clear","upper-lower-mask","critical-status",
                        "critical-clear","critical-mask","valid-mask","trdy",
                        "captured-enable-mask","captured-valid-mask",
                        "sample-pending","deadline-exhausted","upper-lower-irq",
                        "critical-irq","sample-deadline-ns","last-sample-ns",
                        "sample-rounds","complete-rounds")}
        state=item["state"]
        boolean_state={"sample-pending","deadline-exhausted",
                       "upper-lower-irq","critical-irq"}
        if any(type(value) is not (bool if key in boolean_state else int)
               for key,value in state.items()):
            raise RuntimeError("TSENS state has unexpected QOM types")
        if not (0<=state["complete-rounds"]<=state["sample-rounds"]):
            raise RuntimeError("TSENS sample counters are inconsistent")
        item["sensors"]=[]
        for index in range(16):
            sensor={"index":index}
            sensor["observed"]={key:command("qom-get",{"path":path,
                    "property":f"sensor{index}-{key}"})
                for key in ("input-millicelsius","input-valid","last-code",
                            "captured-code","last-millicelsius","valid","status",
                            "threshold","critical-threshold")}
            observed=sensor["observed"]
            if any(type(value) is not (bool if key in ("valid","input-valid") else int)
                   for key,value in observed.items()):
                raise RuntimeError("TSENS sensor has unexpected QOM types")
            code=observed["last-code"]
            if not 0<=code<=0xfff:
                raise RuntimeError("TSENS sample code is outside its 12-bit field")
            signed_code=code-4096 if code & 0x800 else code
            sensor["valid_fields_agree"]=(observed["valid"]==
                bool(state["valid-mask"] & (1 << index))==
                bool(observed["status"] & (1 << 21)))
            sensor["last_code_matches_status_and_millicelsius"]=(
                code==(observed["status"] & 0xfff)
                and observed["last-millicelsius"]==signed_code*100)
            item["sensors"].append(sensor)
        item["known_input_count"]=sum(x["observed"]["input-valid"] for x in item["sensors"])
        item["valid_sensor_count"]=sum(x["observed"]["valid"] for x in item["sensors"])
        item["all_sample_fields_consistent"]=all(x["valid_fields_agree"] and
            x["last_code_matches_status_and_millicelsius"] for x in item["sensors"])
        item["completed_reference_sampling_observed"]=(
            config["enabled-reference"] is True and item["geometry_matches_actual_dt"]
            and config["sensor-count"]==16 and config["sampling-period-ns"]==1000000
            and state["sample-rounds"]>0 and state["complete-rounds"]>0
            and state["last-sample-ns"]>=0 and state["trdy"]==8
            and item["valid_sensor_count"]>0 and item["all_sample_fields_consistent"])
        report["controllers"].append(item)
    report["stopped_status_after"]=command("query-status")
    if report["stopped_status_after"].get("running") is not False:
        raise RuntimeError("TSENS guest resumed during observation")
    report["controller_count"]=len(report["controllers"])
    report["geometry_matches"]=sum(x["geometry_matches_actual_dt"] for x in report["controllers"])
    report["controllers_with_completed_reference_sampling"]=sum(
        x["completed_reference_sampling_observed"] for x in report["controllers"])
    report["interpretation"]="Sampling counters, captured inputs, VALID and vendor TRDY are raw digital-reference observations. The boot mask, 25 C environment and 1 ms period are explicit model choices, not measured tablet state or analog calibration. DT thermal references establish consumer channels only; kernel logs independently establish native probe progress. IRQ metadata records DT wiring, not a native interrupt-delivery test."
    return report
CPUFREQ_DOMAINS=json.loads("[{\"path\":\"/machine/cpufreq0\",\"domain_id\":0,\"memory_base\":408489984,\"resource_size\":4096,\"related_cpu_mask\":15,\"cpu_ids\":[0,1,2,3],\"max_cores\":4,\"core_counter_count\":4,\"spi\":30,\"gic_intid\":62,\"interrupt_flags\":4},{\"path\":\"/machine/cpufreq1\",\"domain_id\":1,\"memory_base\":408494080,\"resource_size\":4096,\"related_cpu_mask\":112,\"cpu_ids\":[4,5,6],\"max_cores\":4,\"core_counter_count\":3,\"spi\":31,\"gic_intid\":63,\"interrupt_flags\":4},{\"path\":\"/machine/cpufreq2\",\"domain_id\":2,\"memory_base\":408498176,\"resource_size\":4096,\"related_cpu_mask\":128,\"cpu_ids\":[7],\"max_cores\":4,\"core_counter_count\":1,\"spi\":19,\"gic_intid\":51,\"interrupt_flags\":4}]")
def capture_cpufreq_hw(command):
    """Record one stopped QOM snapshot; never infer native driver success."""
    report={
        "scope":"Stopped CPUFreq EPSS reference identity and raw QOM state",
        "guest_memory_written":False,"device_mmio_read":False,
        "host_environment_modified_by_observer":False,
        "guest_cpu_executed_by_observer":False,
        "factory_lut_observed":False,"analog_voltage_measured":False,
        "native_driver_success_assessed":False,"userspace_success_assessed":False,
        "counter_progress_observed":False,"native_irq_delivery_proven":False,
        "android_boot_passed":False,
        "dtb_sha256":"5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "bounded_dt_capture_blob":"541c58de5c6711215cd6ebc24e48a11ea4a69127",
        "domains":[]}

    def stopped(value):
        return (type(value) is dict and value.get("running") is False
                and type(value.get("status")) is str and bool(value["status"]))

    def typed(path, name, kind):
        value=command("qom-get",{"path":path,"property":name})
        if kind=="bool":
            good=type(value) is bool
        elif kind=="int64":
            good=type(value) is int and -(1 << 63)<=value<(1 << 63)
        else:
            bits=32 if kind=="uint32" else 64
            good=type(value) is int and 0<=value<(1 << bits)
        if not good:
            raise RuntimeError("CPUFreq "+path+"/"+name+" has unexpected QOM type/range")
        return value

    def rate(xo,lval):
        return xo*lval if xo and xo<=((1000000000 << 32)//lval) else 0

    before=command("query-status")
    report["stopped_status"]=before
    if not stopped(before):
        raise RuntimeError("CPUFreq observation requires a stopped guest")
    try:
        for expected in CPUFREQ_DOMAINS:
            item=dict(expected)
            path=item["path"]
            config_fields={
                "enabled-reference":"bool","domain-id":"uint32",
                "reference-boot-perf-index":"uint32","memory-base":"uint64",
                "resource-size":"uint32","related-cpu-mask":"uint32",
                "max-cores":"uint32","core-counter-count":"uint32",
                "lut-max-entries":"uint32","lut-row-size":"uint32",
                "valid-perf-count":"uint32"}
            config={key:typed(path,key,kind) for key,kind in config_fields.items()}
            item["configuration"]=config
            item["geometry_matches_actual_dt"]=all((
                config["domain-id"]==item["domain_id"],
                config["memory-base"]==item["memory_base"],
                config["resource-size"]==item["resource_size"],
                config["related-cpu-mask"]==item["related_cpu_mask"],
                config["max-cores"]==item["max_cores"],
                config["core-counter-count"]==item["core_counter_count"],
                config["lut-max-entries"]==40,
                config["lut-row-size"]==4))
            item["reference_configuration_matches"]=(
                config["enabled-reference"] is True
                and 0<=config["reference-boot-perf-index"]<4
                and config["valid-perf-count"]==4)
            item["raw_state_recorded"]=False
            item["reference_state_fields_consistent"]=False
            if not config["enabled-reference"]:
                item["raw_state_unavailable"]="unknown-profile"
                report["domains"].append(item)
                continue

            state_fields={
                "xo-frequency-hz":"uint64","alternate-frequency-hz":"uint64",
                "performance-index":"uint32","requested-lval":"uint32",
                "effective-lval":"uint32","requested-frequency-hz":"uint64",
                "frequency-hz":"uint64","selected-voltage-microvolt":"uint32",
                "interrupt-status":"uint32","frequency-limit-lval":"uint32",
                "limited":"bool","frequency-known":"bool","irq-level":"bool",
                "counter":"uint32","counter-fraction":"uint32",
                "counter-value-at-epoch":"uint32",
                "counter-fraction-at-epoch":"uint32","counter-epoch-ns":"int64"}
            state={key:typed(path,key,kind) for key,kind in state_fields.items()}
            if (state["performance-index"]>=4 or state["frequency-limit-lval"]>255
                    or state["requested-lval"]>255 or state["effective-lval"]>255
                    or state["interrupt-status"] & ~4
                    or state["counter-fraction"]>=1000000000
                    or state["counter-fraction-at-epoch"]>=1000000000):
                raise RuntimeError("CPUFreq raw state exceeds its reference fields")
            item["state"]=state
            item["lut_rows"]=[]
            expected_lvals=(16,32,64,96)
            expected_mv=(750,800,900,1000)
            for row in range(40):
                raw={key:typed(path,f"lut{row}-{key}",kind) for key,kind in
                     (("frequency-word","uint32"),("voltage-word","uint32"),
                      ("frequency-hz","uint64"))}
                reference_index=min(row,3)
                item["lut_rows"].append({
                    "index":row,"observed":raw,
                    "matches_declared_reference_row":(
                        raw["frequency-word"]==((1 << 30) | (4 << 16)
                                                | expected_lvals[reference_index])
                        and raw["voltage-word"]==expected_mv[reference_index]
                        and raw["frequency-hz"]==rate(state["xo-frequency-hz"],
                                                       expected_lvals[reference_index]))})
            item["all_lut_rows_match_declared_reference"]=all(
                x["matches_declared_reference_row"] for x in item["lut_rows"])
            # The first duplicate is row 4, whose value equals valid row 3.
            item["declared_reference_terminal_row_index"]=4
            item["core_counters"]=[
                {"local_index":local,"cpu_id":cpu,
                 "register_offset":0x3c8+4*local,
                 "value":typed(path,f"core{local}-counter","uint32")}
                for local,cpu in enumerate(item["cpu_ids"])]
            selected=state["performance-index"]
            requested=expected_lvals[selected]
            cap=state["frequency-limit-lval"]
            effective=min(requested,cap) if cap else requested
            calculated_rate=rate(state["xo-frequency-hz"],effective)
            item["reference_state_fields_consistent"]=all((
                state["requested-lval"]==requested,
                state["effective-lval"]==effective,
                state["requested-frequency-hz"]==rate(state["xo-frequency-hz"],requested),
                state["frequency-hz"]==calculated_rate,
                state["selected-voltage-microvolt"]==expected_mv[selected]*1000,
                state["frequency-known"]==(calculated_rate!=0),
                state["limited"]==bool(calculated_rate and effective<requested),
                state["irq-level"]==bool(state["interrupt-status"] & 4),
                all(c["value"]==state["counter"] for c in item["core_counters"])))
            # A latched IRQ may remain after the restriction ends, or W1C may
            # clear it during an unchanged restriction. Do not equate it to limited.
            item["raw_state_recorded"]=True
            report["domains"].append(item)
    finally:
        after=command("query-status")
        report["stopped_status_after"]=after
        if not stopped(after) or after["status"]!=before["status"]:
            raise RuntimeError("CPUFreq guest status changed during observation")
    report["domain_count"]=len(report["domains"])
    report["geometry_matches"]=sum(x["geometry_matches_actual_dt"] for x in report["domains"])
    report["reference_configuration_matches"]=sum(
        x["reference_configuration_matches"] for x in report["domains"])
    report["domains_with_raw_state"]=sum(x["raw_state_recorded"] for x in report["domains"])
    report["domains_with_consistent_reference_state"]=sum(
        x["raw_state_recorded"] and x["reference_state_fields_consistent"]
        and x.get("all_lut_rows_match_declared_reference",False)
        for x in report["domains"])
    report["domains_with_known_frequency"]=sum(
        x.get("state",{}).get("frequency-known") is True for x in report["domains"])
    report["observation_status"]=(
        "complete" if report["domain_count"]==3
        and report["geometry_matches"]==3
        and report["reference_configuration_matches"]==3
        and report["domains_with_consistent_reference_state"]==3 else "inconclusive")
    report["interpretation"]=(
        "This is a stopped raw QOM snapshot, not a CPUFreq probe, policy, userspace, "
        "frequency-transition or interrupt-delivery verdict. The four all-XO LUT "
        "states, voltage words, limit input, always-clocked per-core counters and "
        "edge-latched IRQ are declared reference policies, not measured tablet "
        "firmware or analog behavior. A single counter value does not prove "
        "progress, and a nonzero requested index does not identify its writer. "
        "The alternate GPLL0 input is reported but no row in this profile uses "
        "src=0. DT SPI/INTID fields describe wiring only. Native kernel progress "
        "must be assessed separately from printk and explicit userspace evidence.")
    return report
def capture_with_mailbox(command,machine):
    report=capture_aop_peer(command,machine)
    if report.get("stopped_status_after",{}).get("running") is False:
        try:
            report["private_unconsumed_mailbox"]=command(
                "human-monitor-command",
                {"command-line":"xp /256bx 0x0c300200"})
            report["private_mailbox_scope"]="Read-only fixed reference APSS mailbox; do not publish raw payload"
        except Exception as exc:
            report["private_mailbox_error"]=type(exc).__name__+": "+str(exc)
        try:
            report["noc_qos_observer"]=capture_noc(command)
        except Exception as exc:
            report["noc_qos_observer_error"]=type(exc).__name__+": "+str(exc)
        try:
            report["smmuv500_observer"]=capture_apps_smmuv500(command)
        except Exception as exc:
            report["smmuv500_observer_error"]=type(exc).__name__+": "+str(exc)
        try:
            report["tsens24xx_observer"]=capture_tsens24xx(command)
        except Exception as exc:
            report["tsens24xx_observer_error"]=type(exc).__name__+": "+str(exc)
        try:
            report["cpufreq_hw_observer"]=capture_cpufreq_hw(command)
        except Exception as exc:
            report["cpufreq_hw_observer_error"]=type(exc).__name__+": "+str(exc)
    return report
diagnose_kernel.capture_aop_peer=capture_with_mailbox
raise SystemExit(diagnose_kernel.main())
