"""Prepare real MuMu media and per-case documentation; does not run app tests."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
REGIONS = {
    "upper": (175, 300, 905, 1100),
    "lower": (175, 1020, 905, 1760),
    "neck": (300, 820, 780, 1270),
    "stream": (375, 985, 705, 1755),
    "landing": (175, 1020, 905, 1760),
}
GROUP_REGIONS = {
    "upper": "upper", "lower": "lower", "neck": "neck",
    "upper-neck": "neck", "neck-stream": "neck", "stream": "stream",
    "landing": "landing", "state": "full", "cross": "full"
}
STAGE_NAMES = {
    "initial": "未开始，50 / 50",
    "early": "开始后约 5 秒，显示 45 / 50",
    "middle": "开始后约 25 秒，显示 25 / 50",
    "late": "开始后约 45 秒，显示 5 / 50",
    "tail": "开始后约 49 秒，显示 1 / 50",
    "after_zero": "开始后约 51 秒",
    "settled": "开始后约 56 秒的完成状态",
    "running_before_pause": "暂停前的运行状态",
    "paused": "暂停后",
    "paused_later": "暂停后继续等待",
    "resumed": "恢复运行后",
    "final_paused": "采集结束时暂停保留"
}
CASE_STAGES = {
    "U00": ["initial", "early", "middle", "late", "settled"],
    "U01": ["initial", "early", "middle", "late", "tail", "settled"],
    "L00": ["initial", "early", "middle", "late", "settled"],
    "L01": ["initial", "early", "middle", "late", "tail", "settled"],
    "N00": ["initial", "middle", "tail", "settled"],
    "N02": ["initial", "early", "tail", "settled"],
    "UN02": ["initial", "early", "tail", "settled"],
    "NS02": ["initial", "early", "tail", "settled"],
    "S00": ["early", "middle", "late", "tail", "settled"],
    "S03": ["early", "middle", "late", "tail"],
    "SL00": ["early", "middle", "late", "settled"],
    "C00": ["initial", "early", "middle", "late", "settled"],
    "C01": ["running_before_pause", "paused", "paused_later", "resumed"],
    "C02": ["initial", "final_paused"],
    "C03": ["late", "tail", "after_zero", "settled"],
    "X01": ["initial"],
}
ACTUAL_PHENOMENA = {
    "U00": "初始上沙接近满球；中段上部空腔扩大、下部仍是整团沙；完成图中上球排空。",
    "U01": "45/50、25/50、5/50 与 1/50 阶段实图已采集，上沙逐阶段减少；具体凹陷与连续变化待结合录像审阅。",
    "U02": "阶段图可见上沙细碎纹理，已留下原速短循环及完整录像；刷新闪烁和动态连续性待审阅。",
    "U03": "各阶段全宽图保留左右玻璃与沙面，能定位贴壁边界；缺不同起伏档位与独立边界量测。",
    "L00": "初始下球空；早期球底有小堆，中段堆高与两侧展开增加，完成图保留大块积沙。",
    "L01": "同局阶段图显示下堆由小到大、坡面随填充变化；沙量与单调性还未做独立读数。",
    "L02": "中段图同时可见堆顶、坡面与飞溅，已录下球短循环；纹理是否有跳切仍待录像审阅。",
    "L03": "完整下球图可见两侧外缘及球底，完成沙体留在玻璃内；不同档位和遮罩误差未量测。",
    "N00": "未开始颈内为空，运行时有颈内沙，完成图颈内排空；上下玻璃过渡同屏。",
    "N01": "50 秒周期的内孔与两侧玻璃可见；没有短长周期对照或内孔总宽独立读数。",
    "N02": "有空颈、运行、末段与完成图，并录开局和结束片段；注入与排空连续性待逐帧确认。",
    "N03": "运行颈部可见沙色和玻璃边，已录颈内短循环及暂停恢复全屏；速度与回退路径未量测。",
    "UN00": "运行阶段上沙沿上喇叭口连到颈部，完成图上球与颈部为空；整体供沙动态待审阅。",
    "UN01": "新图保留上沙底部、喇叭口、直颈与左右玻璃；有早中晚阶段，缺改前同条件和接缝量测。",
    "UN02": "首次释放和末段录像已录，静态图覆盖注入前及耗尽后；具体接通和排空顺序待逐帧确认。",
    "NS00": "运行全图可从颈部追到沙柱和堆面；开局与停止释放过程已录，尚未审阅。",
    "NS01": "颈部及出生带有早中晚同倍率图；没有内孔、出生带和笔画外缘的独立数值。",
    "NS02": "已录从未开始到落沙的开局片段，以及末段到完成；头部形态和在途沙收尾待逐帧确认。",
    "S00": "早中晚图有从出口到堆面的沙柱，完成图下落段消失；整股流动观感已录像，待审阅。",
    "S01": "早中晚全长沙柱原图可用于分带看方向和宽度；未做背景行读数和有效负对照。",
    "S02": "已录自由下落窗口的原速循环片段；没有独立速度标记或带应用时间戳的无损连续帧。",
    "S03": "有早中晚及末段沙柱图与短片；末段颗粒量、各配色和默认/回退路径未完整核对。",
    "SL00": "同屏图中沙柱落到沙堆，堆面随进度抬高；完成图落地活动消失，整体过程已录待审阅。",
    "SL01": "各阶段可见落点及实际堆面，完成状态有原图；碰撞线、误差及横缝负对照未独立测量。",
    "SL02": "各阶段图可见堆顶周围飞溅，已录中段循环及暂停恢复；生成方向、反弹与积浓需进一步确认。",
    "C00": "同一局从上球满、下球空到上球空、下球积沙的原速全过程已录；其他周期和组合未覆盖。",
    "C01": "已录运行、暂停等待、恢复及再次暂停，保留全屏计时与各部位；音频无记录，其他进度未覆盖。",
    "C02": "本轮操作了重置后开始新局；周期弹窗取消、收尾重置与性能测试取消未执行。",
    "C03": "50 秒局末段至完成已录；完成后全图显示 0/50、上球与颈部空、下球保留积沙；短长周期和提醒音未测。",
    "X00": "此配置的整套画面与全局流动链已有阶段图及录像；没有改前或其他组合对照。",
    "X01": "只有金沙这一配置，未逐项执行其他配色、A-F 档位和极端组合。",
    "X02": "真正安装的 2.96 APK 可运行，1080×1920 竖屏控件及字体可见；其他尺寸和方向未测。",
    "X03": "只有本版单配置定位图；未执行前后物理、性能、听音及未改区域对照。"
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evidence(path, label):
    return {"path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "label": label}


def prepare(manifest_path, ffmpeg):
    manifest_path = manifest_path.resolve()
    manifest_path.relative_to(ROOT)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("package_version") != "2.96" or not manifest.get("runtime_matches_apk"):
        raise ValueError("Only verified 2.96 installed-APK captures can refresh current rules")
    media = manifest_path.parent
    session = media.name.removeprefix("v2.96-")
    image_dir = ROOT / "design/images/mumu-2.96-20261013" / ("session-" + session)
    catalog_path = ROOT / "design/checks/rules/sand-flow-cases.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if data["revision"] != "2026-10-13.2":
        raise ValueError("This media refresh targets the existing 33-case draft only")

    def encode(source, output, start=None, length=None, crop=None):
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        if start is not None:
            command += ["-ss", str(start)]
        command += ["-i", str(source)]
        if length is not None:
            command += ["-t", str(length)]
        if crop:
            left, top, right, bottom = crop
            command += ["-vf", "crop=%d:%d:%d:%d" %
                        (right - left, bottom - top, left, top)]
        command += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-threads", "2", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                    "-movflags", "+faststart", str(output)]
        subprocess.run(command, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print("MEDIA " + output.relative_to(ROOT).as_posix(), flush=True)

    originals = {name: media / (name + ".mp4") for name in ("full-cycle", "pause-resume")}
    for name, source in originals.items():
        encode(source, media / (name + "-view.mp4"))

    clips = {}
    loop_regions = {
        "upper": REGIONS["upper"], "lower": REGIONS["lower"], "neck": REGIONS["neck"],
        "stream": (465, 1090, 615, 1270), "landing": (250, 1180, 830, 1760)
    }
    start = manifest["cycle_start_offset"]
    for name, crop in loop_regions.items():
        path = media / (name + "-loop.mp4")
        encode(originals["full-cycle"], path, start + 14, 8, crop)
        width, height = crop[2] - crop[0], crop[3] - crop[1]
        poster = image_dir / (name + "-loop-poster.png")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                        "-i", str(path), "-frames:v", "1", str(poster)],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        label = {"upper": "上沙整体运动", "lower": "下沙堆积与坡面",
                 "neck": "颈部与上下连接", "stream": "自由下落颗粒",
                 "landing": "落地与飞溅"}[name]
        clips[name] = {
            "path": path.relative_to(ROOT).as_posix(),
            "poster": poster.relative_to(ROOT).as_posix(),
            "width": width, "height": height, "loop": True,
            "caption": "%s · v2.96 安装版，原速 8 秒循环片段；源录像偏移约 %.2f–%.2f 秒。"
                       "结尾回到开头是播放器循环，不是应用重新开始。无声，压缩画面不用作像素判据。" %
                       (label, start + 14, start + 22)
        }
    for name, offset, length in (("opening", max(0, start - 0.5), 4),
                                  ("ending", start + 44, 14)):
        path = media / (name + ".mp4")
        encode(originals["full-cycle"], path, offset, length)
        clips[name] = {
            "path": path.relative_to(ROOT).as_posix(),
            "poster": (image_dir / ("initial.png" if name == "opening" else "late.png"))
                      .relative_to(ROOT).as_posix(),
            "width": 1080, "height": 1920, "loop": False,
            "caption": "v2.96 安装版%s原速片段，不循环；无声。完整过程见本版录像。" %
                       ("首次释放" if name == "opening" else "末段至完成")
        }
    full_cycle = {
        "path": (media / "full-cycle-view.mp4").relative_to(ROOT).as_posix(),
        "poster": (image_dir / "initial.png").relative_to(ROOT).as_posix(),
        "width": 1080, "height": 1920, "loop": False,
        "caption": "v2.96 安装版完整原速录像：重置后开始，50 秒全过程及完成后等待；"
                   "保留计时和全屏上下文，不跳转推进，不含声音。"
    }
    pause_video = {
        "path": (media / "pause-resume-view.mp4").relative_to(ROOT).as_posix(),
        "poster": (image_dir / "paused.png").relative_to(ROOT).as_posix(),
        "width": 1080, "height": 1920, "loop": False,
        "caption": "v2.96 安装版暂停与恢复原速录像：运行、暂停等待、恢复、再次暂停；"
                   "计时与全部部位同屏。无声，不能据此登记音频通过。"
    }

    def picture(region, stage, case_id):
        source = image_dir / (stage + ".png")
        path = source
        if region != "full":
            path = image_dir / ("%s-%s.png" % (region, stage))
            with Image.open(source) as image:
                image.crop(REGIONS[region]).save(path)
        return {"path": path.relative_to(ROOT).as_posix(),
                "stage": stage,
                "caption": "%s · v2.96 安装版：%s；50 秒、金沙、1080×1920。"
                           "同次采集原图%s，真实进度以画面和采集时间共同核对，不从整数显示反推精确值。" %
                           (case_id, STAGE_NAMES[stage], "裁图" if region != "full" else ""),
                "max_width": 320 if region in ("full", "stream") else 400}

    data["revision"] = "2026-10-13.3"
    data["media_scope_note"] = (
        "每个编号的配图已换为 GitHub 2.96 APK 安装版的真实采集，包含同一局早期、中段、末段和完成。"
        "动态项配原速循环短片或完整录像；没有生成运动或补画沙子。"
        "本轮仅 50 秒金沙竖屏及一次暂停恢复，其他周期、配色、档位、方向、音频和性能仍未完整验证。"
        "配图说明实际版本现象，不自动成为正确标准；通过状态只在版本记录逐项登记。"
    )
    data["case_images"] = {}
    data["case_videos"] = {}
    results = {}
    for group in data["groups"]:
        region = GROUP_REGIONS[group["id"]]
        old_examples = [item for item in group["images"]
                        if "/mumu-2.96-20261013/" not in item["path"]]
        group["images"] = [picture(region, "middle", "部位全貌")] + old_examples
        for case in group["cases"]:
            case_id = case["id"]
            stages = CASE_STAGES.get(case_id, ["early", "middle", "late"])
            data["case_images"][case_id] = [picture(region, stage, case_id) for stage in stages]
            if case_id in {"U00", "U01", "U02", "L00", "L01", "L02", "N00", "N02", "N03",
                           "UN00", "UN02", "NS00", "NS02", "S00", "S02", "S03", "SL00", "SL02"}:
                key = "lower" if case_id == "S00" else region if region in clips else "neck"
                data["case_videos"][case_id] = [clips[key]]
            if case_id in {"N02", "UN02", "NS02"}:
                data["case_videos"][case_id] += [clips["opening"], clips["ending"]]
            if case_id == "C00":
                data["case_videos"][case_id] = [full_cycle]
            elif case_id == "C01":
                data["case_videos"][case_id] = [pause_video]
            elif case_id == "C02":
                data["case_videos"][case_id] = [pause_video]
            elif case_id == "C03":
                data["case_videos"][case_id] = [clips["ending"], full_cycle]
            actual = ACTUAL_PHENOMENA[case_id] + "仅本配置素材观察，尚未完成本项验收。"
            status = "partial"
            if case_id in {"X01", "X03"}:
                status = "not_tested"
            case_evidence = [evidence(ROOT / item["path"], "本项阶段实图")
                             for item in data["case_images"][case_id]]
            case_evidence += [evidence(ROOT / item["path"], "本项原速动态资料")
                              for item in data["case_videos"].get(case_id, [])]
            results[case_id] = {"status": status, "actual": actual,
                                "checked_by": "Codex（素材采集，非验收签字）",
                                "checked_at": manifest["started_at"], "evidence": case_evidence}

    # Original compressed recorder files stay recoverable without bloating the formal pages.
    archives = ROOT / "_temp/mumu-media" / session
    archives.mkdir(parents=True, exist_ok=False)
    for name, source in originals.items():
        target = archives / (name + "-raw.mp4")
        source.resolve().relative_to(ROOT)
        target.resolve().relative_to(ROOT)
        source.replace(target)
        for item in manifest["files"]:
            if item["path"] == source.relative_to(ROOT).as_posix():
                item["path"] = target.relative_to(ROOT).as_posix()
        manifest.setdefault("raw_archives", []).append(evidence(target, "原始录屏，临时保留"))
    manifest["prepared_at"] = datetime.now().astimezone().isoformat()
    manifest["prepared_media"] = {
        "full_cycle": full_cycle, "pause_resume": pause_video, "clips": clips}
    write_json(manifest_path, manifest)
    write_json(catalog_path, data)

    record_dir = ROOT / "design/checks/records/v2.96"
    snapshot = record_dir / "evidence" / ("rules-" + data["revision"] + ".json")
    if snapshot.exists():
        raise ValueError("Refusing to replace a frozen rules snapshot")
    write_json(snapshot, data)
    record = {
        "schema": 1, "date": manifest["started_at"][:10],
        "session": "GitHub APK 安装版录像补录 " + session,
        "source_version": "2.96", "package_version": "2.96",
        "environment": "MuMu 127.0.0.1:16416，1080×1920，density 480，已重装 GitHub APK",
        "rules_revision": data["revision"], "rules_snapshot": snapshot.relative_to(ROOT).as_posix(),
        "rules_snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "source_identity": evidence(manifest_path, "APK 及运行代码核对记录"),
        "review_status": "pending",
        "conclusion": "本轮是真正安装的 GitHub 2.96 APK，不是旧容器直推源码。"
                      "补齐 33 个编号的实图与适用动态资料，但没有完整矩阵验收；当前没有编号登记为通过。",
        "screenshots": [picture("full", stage, "全屏") for stage in
                        ("initial", "middle", "late", "settled", "paused", "resumed")],
        "videos": [full_cycle, pause_video], "results": results, "observations": [],
        "scope": ["核对 APK SHA256、包版本及解包运行代码，GitHub 发布提交 7ee12ad。",
                  "按真实时间录制 50 秒金沙完整一局，没有跳转进度；记录初始、约 10%、50%、90%、末段和完成。",
                  "另录运行、暂停等待、恢复及再次暂停，采集结束保持暂停。",
                  "所有短循环从完整录像原速裁出；图像裁剪保留原始像素，没有生成沙子或运动。"],
        "historical_automation": ["本轮不在 PC 启动应用或运行测试；未重跑 CI 或性能对照。",
                                  "此前 2.94 容器与旧 PC 报告单独保留，不用于本轮通过结论。"],
        "limitations": ["仅一个周期、金沙、一个竖屏尺寸；配置原始值见采集记录，不把存储数字猜成 A-F。",
                        "Android screenrecord 无音轨，不能验证循环音、完成播报或暂停音频。",
                        "视频经过有损编码，只用于动态观感；像素及几何判断用原始 PNG 和独立读数。",
                        "录屏增加运行负担，不以录像帧率证明应用性能。",
                        "素材齐备不等于规则通过；其他组合、量测、对照和人工审阅未齐。"],
        "deployment": manifest_path.relative_to(ROOT).as_posix()
    }
    record_path = record_dir / (session + ".json")
    if record_path.exists():
        raise ValueError("Refusing to replace an existing recording record")
    write_json(record_path, record)
    print("MEDIA_RECORD " + record_path.relative_to(ROOT).as_posix(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--add-landing-context", action="store_true",
                        help="Add full lower-bulb context without replacing recorded evidence")
    args = parser.parse_args()
    if args.add_landing_context:
        add_landing_context(args.manifest)
    elif args.ffmpeg:
        prepare(args.manifest, args.ffmpeg)
    else:
        parser.error("--ffmpeg is required when preparing clips")


def add_landing_context(manifest_path):
    session = manifest_path.parent.name.removeprefix("v2.96-")
    catalog_path = ROOT / "design/checks/rules/sand-flow-cases.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if data["revision"] != "2026-10-13.3":
        raise ValueError("Context addition only applies to the current recording draft")
    record_path = ROOT / "design/checks/records/v2.96" / (session + ".json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for case_id in ("SL00", "SL01", "SL02"):
        for item in data["case_images"][case_id]:
            path = (ROOT / item["path"]).parent / ("lower-" + item["stage"] + ".png")
            item["path"] = path.relative_to(ROOT).as_posix()
            item["caption"] += "保留整个下球，末段落点上移也不能裁出观察范围。"
            if not any(row["path"] == item["path"] for row in record["results"][case_id]["evidence"]):
                record["results"][case_id]["evidence"].append(evidence(path, "完整下球与落点上下文"))
    data["revision"] = "2026-10-13.4"
    snapshot = record_path.parent / "evidence/rules-2026-10-13.4.json"
    if snapshot.exists():
        raise ValueError("Refusing to replace a frozen snapshot")
    write_json(snapshot, data)
    record["rules_revision"] = data["revision"]
    record["rules_snapshot"] = snapshot.relative_to(ROOT).as_posix()
    record["rules_snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    record["coverage_update_note"] = (
        "落地相关编号补上完整下球上下文，防止末段落点升高后被固定局部窗口裁掉。"
        "原截图、原录像及旧规则快照保留，逐项测试状态没有因此改成通过。"
    )
    write_json(catalog_path, data)
    write_json(record_path, record)
    print("LANDING_CONTEXT_ADDED " + record_path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
