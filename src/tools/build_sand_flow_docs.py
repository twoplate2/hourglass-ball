"""Build static sand-flow rule and record pages without starting the app or tests."""

import argparse
from datetime import date
import hashlib
from html import escape
import json
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ROOT / "design/checks"
CASES = CHECKS / "rules/sand-flow-cases.json"
STATUSES = {
    "not_tested": ("未测", ""),
    "partial": ("片段观察，未完成", " partial"),
    "failed": ("不通过", ""),
    "interrupted": ("中断，未完成", ""),
    "pending_review": ("待验收", " partial"),
    "passed": ("通过（具名登记）", " passed"),
}


def link(path, output):
    return escape(os.path.relpath(ROOT / path, output.parent).replace("\\", "/"), quote=True)


def listing(items):
    return "<ul>" + "".join("<li>%s</li>" % escape(item) for item in items) + "</ul>"


def pictures(items, output, three=False):
    content = []
    for item in items:
        if "crop" in item:
            with Image.open(ROOT / item["source"]) as original:
                left, top, right, bottom = item["crop"]
                if not (0 <= left < right <= original.width and
                        0 <= top < bottom <= original.height):
                    raise ValueError("Crop exceeds source image: " + item["path"])
                original.crop(item["crop"]).save(ROOT / item["path"])
        width = (' style="max-width:%dpx"' % item["max_width"]
                 if "max_width" in item else "")
        source = link(item["path"], output)
        with Image.open(ROOT / item["path"]) as image:
            natural_width, natural_height = image.size
        content.append(
            '<figure><a href="%s"><img src="%s" alt="%s" width="%d" height="%d" loading="lazy"%s></a>'
            '<figcaption>%s</figcaption></figure>' %
            (source, source, escape(item["caption"], quote=True),
             natural_width, natural_height, width,
             escape(item["caption"])))
    return '<div class="picture-grid%s">%s</div>' % (
        " three portrait" if three else "", "".join(content))


def case_pictures(items, output, three=False):
    if len(items) <= 3:
        return pictures(items, output, three=three)
    middle = next((item for item in items if item.get("stage") == "middle"), items[1])
    visible = [items[0], middle]
    remaining = [item for item in items if item not in visible]
    return (pictures(visible, output) +
            '<details class="more-stages"><summary>其余阶段实图（%d 张）</summary>%s</details>' %
            (len(remaining), pictures(remaining, output)))


def videos(items, output):
    content = []
    for item in items:
        source = link(item["path"], output)
        poster = link(item["poster"], output)
        content.append(
            '<figure class="video-figure%s"><video width="%d" height="%d" controls muted '
            'playsinline preload="none" poster="%s"%s aria-label="%s">'
            '<source src="%s" type="video/mp4"><a href="%s">打开录像</a></video>'
            '<figcaption>%s</figcaption><p class="small"><a href="%s">打开视频文件</a></p>'
            '</figure>' %
            (" tall" if item["height"] > item["width"] * 1.4 else "",
             item["width"], item["height"], poster, " loop" if item.get("loop") else "",
             escape(item["caption"], quote=True), source, source, escape(item["caption"]), source))
    return '<div class="video-grid">%s</div>' % "".join(content)


def document(title, body, output, navigation):
    stylesheet = link("design/checks/style.css", output)
    nav = "".join('<li><a href="%s">%s</a></li>' %
                  (link(path, output), escape(label)) for label, path in navigation)
    text = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>%s</title>\n<link rel="stylesheet" href="%s">\n</head>\n<body><main>\n'
        '<header><h1>%s</h1><nav aria-label="资料导航"><ul>%s</ul></nav></header>\n'
        '%s\n<footer>由 sand-flow-cases.json 与版本记录生成。修改后运行 '
        'python -B src/tools/build_sand_flow_docs.py；此命令只生成资料，不启动应用或执行测试。'
        '</footer>\n</main></body>\n</html>\n' %
        (escape(title), stylesheet, escape(title), nav, body))
    output.write_text(text, encoding="utf-8")


def case_content(case, data, output, names):
    instructions = '<div><h4>怎么测</h4>%s</div>' % listing(case["steps"])
    expectation = '<div><h4>应当看到什么</h4>%s</div>' % listing(case["expected"])
    blocks = expectation + instructions if case.get("view") == "whole" else instructions + expectation
    items = data.get("case_images", {}).get(case["id"], case.get("images", []))
    images = case_pictures(items, output, three=case["id"] == "C00") if items else ""
    if case.get("known_failure"):
        images = ('<div class="notice warning"><p><strong>已知错误对照：</strong>%s'
                  '</p></div>' % escape(case["known_failure"])) + images
    movies = data.get("case_videos", {}).get(case["id"], [])
    if movies:
        images += '<h4>动态实录</h4>' + videos(movies, output)
    content = (
        '<article class="case%s" id="%s"><h3><span class="case-id">%s</span>%s</h3>'
        '<p class="small">进度：%s</p><p><strong>条件：</strong>%s</p>%s'
        '<div class="case-grid">%s</div>'
        '<h4>需要留下的证据</h4>%s'
        '<p class="small">关联自动检查：%s；人工：%s。这里只是关联，不代表自动完整覆盖。</p>'
        '<p><strong>还要连带看：</strong>%s</p>' %
        (" whole-case" if case.get("view") == "whole" else "",
         case["id"], case["id"], escape(case["title"]),
         "、".join(escape(data["stages"][stage]) for stage in case["stages"]),
         escape(case["setup"]), images, blocks,
         listing(case["evidence"]), "、".join(map(escape, case["automated"])),
         "、".join(map(escape, case["manual"])),
         " · ".join('<a href="#%s">%s %s</a>' %
                    (related, related, escape(names[related]))
                    for related in case["related"])))
    return content + "</article>"


def build_rules(data, output=None):
    output = output or CHECKS / "rules/sand-flow.html"
    groups = data["groups"]
    count = sum(len(group["cases"]) for group in groups)
    names = {case["id"]: case["title"] for group in groups for case in group["cases"]}
    has_whole = "C00" in names
    notice = (
        "先按正常尺寸看整个沙漏，再看各部位整体表现，最后放大检查局部。"
        "一个编号固定对应一项表现，每个版本逐项登记，不能只写整体通过。"
        if has_whole else "每组先看图定位，再逐条看操作和预期。"
    )
    body = [
        '<p class="meta">%s 版规则 · %d 组、%d 个用例 · 规则不是某版测试结果</p>' %
        (escape(data["revision"]), len(groups), count),
        '<div class="notice"><p>' + notice + escape(data.get("media_scope_note",
            '已有截图只覆盖开局附近，动态、完整进度及其他档位需要另外取证；'
            '旧反例只提示错误类型，不直接提供当前通过门槛。')) + '</p></div>',
        '<nav aria-label="用例分类"><ul>' +
        ('<li><a href="#C00">先看全沙漏整体表现 C00</a></li>' if has_whole else "") +
        "".join('<li><a href="#%s">%s</a></li>' %
                (escape(group["id"], quote=True), escape(group["title"]))
                for group in groups) + '</ul></nav>'
    ]
    if has_whole:
        whole = next(case for group in groups for case in group["cases"]
                     if case["id"] == "C00")
        body.extend(['<section><h2>先看整个沙漏</h2>',
                     case_content(whole, data, output, names), '</section>'])
    body.extend([
        '<section id="matrix"><h2>共同的进度与组合</h2>',
        listing(list(data["stages"].values())),
        '<p>周期：%s 秒。配色：%s。颗粒与起伏：分别覆盖 A-F。</p>' %
        (" / ".join(map(str, data["matrix"]["durations_seconds"])),
         "、".join(map(escape, data["matrix"]["colors"]))),
        '<p>%s</p></section>' % escape(data["matrix"]["policy"])
    ])
    if output != CHECKS / "rules/sand-flow.html":
        body.insert(1, '<div class="notice warning"><p>这是归档记录使用的规则快照，'
                    '不是某次执行通过的证明。现行规则见<a href="%s">规则目录</a>；'
                    '后续修改不回填本快照。</p></div>' %
                    link("design/checks/rules/sand-flow.html", output))
    for group in groups:
        body.append('<section id="%s"><h2>%s</h2><p>%s</p>' %
                    (escape(group["id"], quote=True), escape(group["title"]),
                     escape(group["description"])))
        whole_first = group["cases"][0].get("view") == "whole"
        if not whole_first:
            body.append(pictures(group["images"], output))
        for case in group["cases"]:
            if case["id"] != "C00":
                body.append(case_content(case, data, output, names))
            elif has_whole:
                body.append('<p>本组整体表现见页首的<a href="#C00">C00 全沙漏全过程</a>。</p>')
            if whole_first and case.get("view") == "whole":
                body.append('<h3>局部图示与专项表现</h3>')
                body.append(pictures(group["images"], output))
        body.append("</section>")
    document("沙子流动：规则与用例", "\n".join(body), output, [
        ("检查目录", "design/checks/index.html"),
        ("版本记录", "design/checks/records/index.html"),
        ("备注", "design/checks/notes/index.html"),
        ("执行说明", "design/checks/rules/how-to-check.md")
    ])
    return count


def checked_evidence(item):
    path = (ROOT / item["path"]).resolve()
    path.relative_to(ROOT)
    if not path.is_file():
        raise ValueError("Missing evidence: " + item["path"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if item.get("sha256") != digest:
        raise ValueError("Evidence hash mismatch: " + item["path"])


def validate_results(record, ids):
    results = record.get("results", {})
    if set(results) != ids:
        raise ValueError("Each snapshot case needs one result; missing=%s extra=%s" %
                         (sorted(ids - set(results)), sorted(set(results) - ids)))
    for case_id, result in results.items():
        if result.get("status") not in STATUSES or not result.get("actual", "").strip():
            raise ValueError("Invalid status or empty actual phenomenon: " + case_id)
        for item in result.get("evidence", []):
            checked_evidence(item)
        if result["status"] != "passed":
            continue
        for key in ("checked_by", "checked_at", "reviewed_by", "reviewed_at"):
            if not result.get(key, "").strip():
                raise ValueError("Passing result needs " + key + ": " + case_id)
        if not result.get("evidence"):
            raise ValueError("Passing result needs evidence: " + case_id)
        if not record.get("source_identity") or not record.get("rules_snapshot_sha256"):
            raise ValueError("Passing result needs source and rules identity: " + case_id)
        if record.get("environment") in (None, "", "待填写") or record.get(
                "package_version") in (None, "", "未知"):
            raise ValueError("Passing result needs an identified run environment: " + case_id)
        checked_evidence(record["source_identity"])
        checked_evidence({"path": record["rules_snapshot"],
                          "sha256": record["rules_snapshot_sha256"]})


def build_record(data, path):
    record = json.loads(path.read_text(encoding="utf-8"))
    rule_page = "design/checks/rules/sand-flow.html"
    if record.get("rules_snapshot"):
        snapshot = ROOT / record["rules_snapshot"]
        if not snapshot.exists():
            if record["rules_revision"] != data["revision"]:
                raise ValueError("Cannot reconstruct old rules: " + str(snapshot))
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        snapshot_page = snapshot.with_suffix(".html")
        build_rules(data, snapshot_page)
        rule_page = snapshot_page.relative_to(ROOT).as_posix()
    if record["rules_revision"] != data["revision"]:
        raise ValueError("Record needs its original rules snapshot: " + str(path))
    ids = {case["id"] for group in data["groups"] for case in group["cases"]}
    validate_results(record, ids)
    output = path.with_suffix(".html")
    body = [
        '<p class="meta">记录日期：%s · 源码版本 %s · APK 包版本 %s · %s</p>' %
        tuple(escape(record[key]) for key in
              ("date", "source_version", "package_version", "environment")),
        '<div class="notice warning"><p>%s</p></div>' % escape(record["conclusion"]),
        '<p class="small">登记依据：<a href="%s">%s 版规则快照</a>。'
        '下方用例链接指向此快照，不用日后修改的规则回填旧记录。</p>' %
        (link(rule_page, output), escape(record["rules_revision"])),
        '<p><strong>逐项状态：</strong>%s</p>' %
        " · ".join("%s %d 项" % (label, sum(
            result["status"] == status for result in record["results"].values()))
                   for status, (label, _) in STATUSES.items()),
        '<p class="small">“通过”只表示具名检查、审阅和证据登记满足格式要求；'
        '资料生成器只核对文件与摘要，不判断录像内容，也不代替 CI 或发布门禁。</p>',
        '<h2>已有原图</h2>',
        pictures(record["screenshots"], output, three=True) if record["screenshots"]
        else '<p>没有本轮截图，不能复用旧版图片充当已测。</p>',
        '<h2>这批证据能说明什么</h2>', listing(record["scope"]),
        '<h2>每个编号的现象与通过状态</h2>',
        '<p>“片段观察”不是通过；只看静态图不记动态通过，也不代替完整进度。'
        '未覆盖项继续保留，不因补写资料而改变状态。</p>'
    ]
    if record.get("coverage_update_note"):
        body.insert(2, '<div class="notice"><p>%s</p></div>' %
                    escape(record["coverage_update_note"]))
    failures = [case_id for case_id, result in record["results"].items()
                if result["status"] == "failed"]
    if failures:
        body.insert(2, '<p><strong>已确认不通过：</strong>%s</p>' % " · ".join(
            '<a href="#%s">%s</a>' % (escape(case_id, quote=True), escape(case_id))
            for case_id in failures))
    if record.get("capture_rules_snapshot"):
        body.insert(3, '<p class="small"><a href="%s">原采集时的 24 项规则快照</a>'
                    '保留不变；补录整体编号不代表重新执行测试。</p>' %
                    link(record["capture_rules_snapshot"], output))
    if record.get("videos"):
        position = body.index('<h2>每个编号的现象与通过状态</h2>')
        body[position:position] = ['<h2>本轮完整录像</h2>', videos(record["videos"], output)]
    observations = {case: row for row in record["observations"] for case in row["case_ids"]}
    if not set(observations).issubset(ids):
        raise ValueError("Record refers to unknown cases: " + str(path))
    for group in data["groups"]:
        body.append('<section><h3>%s</h3>' % escape(group["title"]))
        for case in group["cases"]:
            observation = observations.get(case["id"])
            result = record["results"][case["id"]]
            status, style = STATUSES[result["status"]]
            verdict = {"passed": "是", "failed": "否"}.get(result["status"], "尚未判定")
            target = link(rule_page, output) + "#" + case["id"]
            body.append(
                '<article class="case" id="%s"><h4><a href="%s">%s %s</a></h4>'
                '<p><strong>是否测试通过：</strong>%s · <span class="status%s">%s</span></p>'
                '<p><strong>实际现象：</strong>%s</p>' %
                (case["id"], target, case["id"], escape(case["title"]),
                 verdict, style, status, escape(result["actual"])))
            if observation:
                body.append('<p class="small">原片段观察：%s</p>' %
                            escape(observation["description"]))
            evidence = result.get("evidence", [])
            if evidence:
                body.append('<p><strong>证据：</strong>%s</p>' % " · ".join(
                    '<a href="%s">%s</a>' %
                    (link(item["path"], output), escape(item.get("label", item["path"])))
                    for item in evidence))
                image_evidence = [dict(item, caption=item.get("label", "本项实图"))
                                  for item in evidence if item["path"].lower().endswith(".png")]
                if image_evidence:
                    body.append('<details class="more-stages"><summary>查看本项实图（%d 张）'
                                '</summary>%s</details>' %
                                (len(image_evidence), pictures(image_evidence, output)))
            else:
                body.append('<p class="small">证据：未登记本项完整执行证据。</p>')
            if result.get("checked_by"):
                body.append('<p class="small">检查人：%s · 检查时间：%s · '
                            '审阅人：%s · 审阅时间：%s</p>' %
                            tuple(escape(result.get(key, "未登记")) for key in
                                  ("checked_by", "checked_at", "reviewed_by", "reviewed_at")))
            body.append('</article>')
        body.append("</section>")
    body.extend([
        '<h2>历史自动检查：单独看，不能拼成通过</h2>',
        listing(record["historical_automation"]),
        '<h2>证据身份与限制</h2>', listing(record["limitations"]),
        '<p><a href="%s">本页原始记录 JSON</a></p>' %
        link(path.relative_to(ROOT).as_posix(), output)
    ])
    references = [(label, record[key]) for key, label in
                  (("automatic_report", "原始报告副本"), ("evidence_readme", "原始日志说明"),
                   ("deployment", "源码部署记录"), ("startup_log", "启动日志"))
                  if record.get(key)]
    if references:
        body.append('<p>%s</p>' % " · ".join('<a href="%s">%s</a>' %
                    (link(target, output), label) for label, target in references))
    if record.get("pause_followup"):
        body.append('<p><a href="%s">暂停后约 2 秒的原图</a></p>' %
                    link(record["pause_followup"], output))
    document("v%s：已有沙流观察记录" % record["source_version"],
             "\n".join(body), output, [
                 ("版本记录目录", "design/checks/records/index.html"),
                 ("规则与用例", "design/checks/rules/sand-flow.html"),
                 ("备注", "design/checks/notes/index.html")
             ])


def new_record(data, version, day):
    if not version or not all(part.isdigit() for part in version.split(".")):
        raise ValueError("Version must contain numeric segments separated by dots")
    stamp = date.fromisoformat(day).strftime("%Y%m%d")
    path = CHECKS / "records" / ("v" + version) / (stamp + ".json")
    if path.exists():
        raise ValueError("Record already exists; refusing to replace it: " + str(path))
    snapshot = path.parent / "evidence" / ("rules-" + data["revision"] + ".json")
    record = {
        "schema": 1, "rules_revision": data["revision"],
        "rules_snapshot": snapshot.relative_to(ROOT).as_posix(),
        "date": day, "source_version": version, "package_version": "未知",
        "environment": "待填写", "review_status": "pending",
        "conclusion": "仅建立逐项记录，尚未执行测试，所有编号默认未测。",
        "screenshots": [], "scope": ["没有本轮执行或观察证据。"],
        "observations": [],
        "results": {case["id"]: {"status": "not_tested",
                                "actual": "未执行，需要按此编号的规则逐项检查。",
                                "evidence": []}
                    for group in data["groups"] for case in group["cases"]},
        "historical_automation": [], "limitations": ["本页不是通过证明。"]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_record_index(paths):
    output = CHECKS / "records/index.html"
    body = ['<p>每个编号固定对应一项表现；每个版本逐项记录实际现象、是否通过和证据。'
            '“未测”“片段观察”“中断”“待验收”均不是通过。</p>']
    entries = [(json.loads(path.read_text(encoding="utf-8")), path) for path in paths]
    entries.sort(key=lambda item: (tuple(map(int, item[0]["source_version"].split("."))),
                                  item[0]["date"], item[1].stem), reverse=True)
    for record, path in entries:
        results = record["results"]
        body.extend([
            '<section><h2>v%s · %s</h2>' %
            (escape(record["source_version"]), escape(record["date"])),
            '<p>%s</p>' % escape(record["conclusion"]),
            '<p class="meta">%s</p>' % escape(record.get("session", "早期片段资料")),
            '<p>%d 个编号 · 通过 %d · 不通过 %d · 尚未完成验收 %d</p>' %
            (len(results), sum(row["status"] == "passed" for row in results.values()),
             sum(row["status"] == "failed" for row in results.values()),
             sum(row["status"] not in ("passed", "failed") for row in results.values()))
        ])
        if record["screenshots"]:
            body.append(pictures(record["screenshots"], output, three=True))
        body.append('<p><a href="%s">查看本版每个编号的现象与结果</a> · '
                    '<a href="%s">原始登记 JSON</a></p></section>' %
                    (link(path.with_suffix(".html").relative_to(ROOT).as_posix(), output),
                     link(path.relative_to(ROOT).as_posix(), output)))
    body.extend([
        '<section><h2>旧版本历史资料</h2><p>历史图片和改动说明不是逐项测试记录，'
        '没有证据的编号不能补写通过。</p><p><a href="history.html">'
        '查看 2.76 至 2.94 的历史索引</a></p></section>',
        '<section><h2>新增版本记录</h2><p>运行资料生成命令 '
        '<code>python -B src/tools/build_sand_flow_docs.py --new-record 版本号</code>，'
        '会列出全部编号，默认未测，保留规则快照；不启动应用、不执行测试。'
        '同版本同日期的现有记录不会被覆盖。</p><p><a href="../notes/record-template.md">'
        '逐项登记格式与状态定义</a></p></section>'
    ])
    document("按版本查看测试记录", "\n".join(body), output, [
        ("检查目录", "design/checks/index.html"),
        ("规则与用例", "design/checks/rules/sand-flow.html"),
        ("备注", "design/checks/notes/index.html")
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-record", metavar="VERSION",
                        help="Create a version record with every case marked not tested")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    if args.new_record:
        created = new_record(data, args.new_record, args.date)
        print("RECORD_CREATED " + created.relative_to(ROOT).as_posix())
    count = build_rules(data)
    records = sorted((CHECKS / "records").glob("v*/[0-9]*.json"))
    for path in records:
        build_record(data, path)
    build_record_index(records)
    print("DOCS_BUILT cases=%d records=%d (no app or tests started)" % (count, len(records)))


if __name__ == "__main__":
    main()
