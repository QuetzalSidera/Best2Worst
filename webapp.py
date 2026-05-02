import io
import json
import os
import tempfile
import threading
import base64
from pathlib import Path
from typing import Any

import tomllib
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for

import main
import storage


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
APP_DB_PATH = os.environ.get("BEST2WORST_DB_PATH", "/app/data/best2worst.db")
APP_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"
DB = storage.connect(APP_DB_PATH)

STATE = {
    "busy": False,
    "stage": "idle",
    "progress": 0.0,
    "message": "",
    "error": "",
    "logs": [],
}
STATE_LOCK = threading.Lock()
RUN_TOKEN = 0
RANK_OPTIONS = ["", "夯", "顶级", "人上人", "NPC", "拉完了"]


class JobInterruptedError(RuntimeError):
    pass


def read_api_keys() -> dict[str, str]:
    if not APP_CONFIG_PATH.exists():
        return {"deepseek_api_key": "", "siliconflow_api_key": ""}
    with APP_CONFIG_PATH.open("rb") as file_handle:
        raw = tomllib.load(file_handle)
    return {
        "deepseek_api_key": raw.get("text_api", {}).get("api_key", ""),
        "siliconflow_api_key": raw.get("tts_api", {}).get("api_key", ""),
    }


def write_api_keys(deepseek_api_key: str, siliconflow_api_key: str) -> None:
    existing = read_api_keys()
    deepseek_api_key = deepseek_api_key or existing["deepseek_api_key"]
    siliconflow_api_key = siliconflow_api_key or existing["siliconflow_api_key"]
    content = [
        "[text_api]",
        f'api_key = {json.dumps(deepseek_api_key, ensure_ascii=False)}',
        "",
        "[tts_api]",
        f'api_key = {json.dumps(siliconflow_api_key, ensure_ascii=False)}',
        "",
    ]
    APP_CONFIG_PATH.write_text("\n".join(content), encoding="utf-8")


def update_state(*, append_log: str | None = None, **kwargs: Any) -> None:
    with STATE_LOCK:
        STATE.update(kwargs)
        if append_log:
            STATE["logs"] = (STATE.get("logs", []) + [append_log])[-100:]


def is_active_run(run_token: int) -> bool:
    with STATE_LOCK:
        return run_token == RUN_TOKEN


def assert_active_run(run_token: int) -> None:
    if not is_active_run(run_token):
        raise JobInterruptedError("任务已被新的执行请求打断。")


def progress_reporter(run_token: int):
    def report(progress: float, message: str) -> None:
        assert_active_run(run_token)
        update_state(progress=round(progress, 2), message=message, append_log=message)

    return report


def run_job(stage: str, fn, run_token: int) -> None:
    update_state(busy=True, stage=stage, progress=0.0, error="", message="", logs=[], append_log=f"开始执行 {stage}")
    try:
        result = fn(progress_reporter(run_token), run_token)
        assert_active_run(run_token)
        message = str(result) if result else "完成"
        update_state(busy=False, stage="idle", progress=100.0, message=message, error="", append_log=message)
    except JobInterruptedError as exc:
        if is_active_run(run_token):
            update_state(busy=False, stage="idle", error=str(exc), message="", append_log=f"中断: {exc}")
    except Exception as exc:  # noqa: BLE001
        if is_active_run(run_token):
            update_state(busy=False, stage="idle", error=str(exc), message="", append_log=f"错误: {exc}")


def build_workspace(
    prompt_body: str,
    *,
    require_text_api_key: bool,
    require_tts_api_key: bool,
) -> tuple[tempfile.TemporaryDirectory, Path, Path, Path]:
    temp_dir = tempfile.TemporaryDirectory(prefix="best2worst_web_")
    root = Path(temp_dir.name)
    config_path = root / "config.toml"
    material_dir = root / "Material"
    output_dir = root / "Output"
    material_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = storage.get_settings(DB)
    api_keys = read_api_keys()
    if require_text_api_key and not api_keys["deepseek_api_key"]:
        raise ValueError("请先在控制台中保存 DeepSeek API Key。")
    if require_tts_api_key and not api_keys["siliconflow_api_key"]:
        raise ValueError("请先在控制台中保存硅基流动 API Key。")

    voice_choice = settings.get("tts_voice", "builtin:Manbo")
    resolved_voice = resolve_voice_choice(voice_choice, root)
    effective_tts_model = resolved_voice["model"] or settings.get("tts_model", "FunAudioLLM/CosyVoice2-0.5B")
    effective_voice = resolved_voice["voice"] or f'{effective_tts_model}:diana'
    reference_audio_path = resolved_voice["reference_audio_path"]
    reference_text = resolved_voice["reference_text"]
    reference_name = resolved_voice["reference_name"]

    config_lines = [
        "[text_api]",
        f'api_key = {json.dumps(api_keys["deepseek_api_key"], ensure_ascii=False)}',
        'base_url = "https://api.deepseek.com"',
        f'model = {json.dumps(settings.get("deepseek_model", "deepseek-chat"), ensure_ascii=False)}',
        "",
        "[tts_api]",
        f'api_key = {json.dumps(api_keys["siliconflow_api_key"], ensure_ascii=False)}',
        'base_url = "https://api.siliconflow.cn/v1"',
        f'model = {json.dumps(effective_tts_model, ensure_ascii=False)}',
        f'voice = {json.dumps(effective_voice, ensure_ascii=False)}',
        f'response_format = {json.dumps(settings.get("tts_format", "mp3"), ensure_ascii=False)}',
        f'speed = {float(settings.get("tts_speed", "1.0"))}',
        f'gain = {float(settings.get("tts_gain", "0.0"))}',
        "",
    ]
    if reference_audio_path is not None:
        config_lines.insert(-1, f'reference_audio = {json.dumps(str(reference_audio_path), ensure_ascii=False)}')
        config_lines.insert(-1, f'reference_text = {json.dumps(reference_text, ensure_ascii=False)}')
        config_lines.insert(-1, f'reference_name = {json.dumps(reference_name, ensure_ascii=False)}')
    else:
        config_lines.insert(-1, 'reference_audio = ""')
        config_lines.insert(-1, 'reference_text = ""')
        config_lines.insert(-1, 'reference_name = ""')
    sample_rate = settings.get("tts_sample_rate", "").strip()
    if sample_rate:
        config_lines.insert(-1, f"sample_rate = {int(sample_rate)}")
    config_path.write_text("\n".join(config_lines), encoding="utf-8")

    material_specs: list[dict[str, Any]] = []
    for order_index, item in enumerate(storage.list_materials(DB), start=1):
        material_path = material_dir / item["filename"]
        material_path.write_bytes(storage.get_material_content(DB, item["id"]))
        material_specs.append(
            {
                "file": item["filename"],
                "order": order_index,
                "name": item["display_name"],
                "rank": item["forced_rank"] or None,
            }
        )

    audio_options = {
        "speed": float(settings.get("tts_speed", "1.0")),
        "reference_text": reference_text,
        "reference_name": reference_name,
        "model": effective_tts_model,
    }
    prompt_markdown = main.compose_prompt_markdown(material_specs, prompt_body, audio_options)
    (material_dir / "prompt.md").write_text(prompt_markdown, encoding="utf-8")
    return temp_dir, config_path, material_dir, output_dir


def resolve_voice_choice(voice_choice: str, workspace_root: Path) -> dict[str, Any]:
    if voice_choice in storage.SILICONFLOW_VOICES:
        voice = storage.SILICONFLOW_VOICES[voice_choice]
        return {
            "model": voice["model"],
            "voice": voice["voice"],
            "reference_audio_path": None,
            "reference_text": None,
            "reference_name": None,
        }
    if voice_choice.startswith("builtin:"):
        voice = storage.BUILTIN_VOICES[voice_choice]
        return {
            "model": None,
            "voice": None,
            "reference_audio_path": main.TEMPLATE_DIR / "Audio" / voice["filename"],
            "reference_text": voice["reference_text"],
            "reference_name": voice["reference_name"],
        }
    if voice_choice.startswith("custom:"):
        voice_id = int(voice_choice.split(":", 1)[1])
        voice = storage.get_custom_voice(DB, voice_id)
        temp_dir = workspace_root / "ReferenceAudio"
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / voice["filename"]
        path.write_bytes(voice["content"])
        return {
            "model": None,
            "voice": None,
            "reference_audio_path": path,
            "reference_text": voice["reference_text"],
            "reference_name": voice["reference_name"],
        }
    raise ValueError("未知语音类型。")


def generate_copywriting_job(progress_callback, run_token: int) -> str:
    prompt_body = storage.get_settings(DB).get("prompt_body", "锐评AI从夯到拉")
    temp_dir, config_path, material_dir, output_dir = build_workspace(
        prompt_body,
        require_text_api_key=True,
        require_tts_api_key=False,
    )
    try:
        with main.use_workspace(config_path, material_dir, output_dir, main.TEMPLATE_DIR):
            payload = main.generate_copywriting_step(progress_callback)
        assert_active_run(run_token)
        storage.save_copywriting(DB, payload)
        return "文案已生成"
    finally:
        temp_dir.cleanup()


def finalize_video_job(progress_callback, run_token: int) -> str:
    prompt_body = storage.get_settings(DB).get("prompt_body", "锐评AI从夯到拉")
    copywriting = storage.get_copywriting(DB)
    if not copywriting:
        raise ValueError("请先生成文案。")
    temp_dir, config_path, material_dir, output_dir = build_workspace(
        prompt_body,
        require_text_api_key=False,
        require_tts_api_key=False,
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Copywriting.json").write_text(json.dumps(copywriting, ensure_ascii=False, indent=2), encoding="utf-8")
        with main.use_workspace(config_path, material_dir, output_dir, main.TEMPLATE_DIR):
            final_video_path = main.finalize_video_step(progress_callback)
            assert_active_run(run_token)
            settings = storage.get_settings(DB)
            audio_path = main.AUDIO_DIR / f"full_audio.{main.audio_extension(settings.get('tts_format', 'mp3'))}"
            storage.save_output(DB, "final_video.mp4", "video/mp4", final_video_path.read_bytes())
            if audio_path.exists():
                storage.save_output(DB, audio_path.name, "audio/mpeg", audio_path.read_bytes())
        return "最终视频已生成"
    finally:
        temp_dir.cleanup()


def generate_audio_job(progress_callback, run_token: int) -> str:
    prompt_body = storage.get_settings(DB).get("prompt_body", "锐评AI从夯到拉")
    copywriting = storage.get_copywriting(DB)
    if not copywriting:
        raise ValueError("请先生成并确认文案。")
    temp_dir, config_path, material_dir, output_dir = build_workspace(
        prompt_body,
        require_text_api_key=False,
        require_tts_api_key=True,
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Copywriting.json").write_text(json.dumps(copywriting, ensure_ascii=False, indent=2), encoding="utf-8")
        with main.use_workspace(config_path, material_dir, output_dir, main.TEMPLATE_DIR):
            audio_path, manifest = main.generate_audio_step(progress_callback)
            assert_active_run(run_token)
            storage.save_output(DB, audio_path.name, "audio/mpeg", audio_path.read_bytes())
            storage.save_output(DB, "manifest.json", "application/json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        return "完整音频已生成"
    finally:
        temp_dir.cleanup()


def generate_video_only_job(progress_callback, run_token: int) -> str:
    prompt_body = storage.get_settings(DB).get("prompt_body", "锐评AI从夯到拉")
    copywriting = storage.get_copywriting(DB)
    manifest_output = storage.get_output(DB, "manifest.json")
    full_audio = find_full_audio_output()
    if not copywriting:
        raise ValueError("请先生成并确认文案。")
    if manifest_output is None or full_audio is None:
        raise ValueError("请先生成音频。")
    manifest = json.loads(manifest_output["content"].decode("utf-8"))
    temp_dir, config_path, material_dir, output_dir = build_workspace(
        prompt_body,
        require_text_api_key=False,
        require_tts_api_key=False,
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Copywriting.json").write_text(json.dumps(copywriting, ensure_ascii=False, indent=2), encoding="utf-8")
        audio_dir = output_dir / "Audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        full_audio_path = audio_dir / full_audio["name"]
        full_audio_path.write_bytes(full_audio["content"])
        with main.use_workspace(config_path, material_dir, output_dir, main.TEMPLATE_DIR):
            final_video_path = main.generate_video_from_manifest_step(manifest, progress_callback)
            assert_active_run(run_token)
            storage.save_output(DB, "final_video.mp4", "video/mp4", final_video_path.read_bytes())
        return "最终视频已生成"
    finally:
        temp_dir.cleanup()


def find_full_audio_output() -> dict[str, Any] | None:
    for name in ["full_audio.mp3", "full_audio.wav", "full_audio.opus", "full_audio.pcm"]:
        output = storage.get_output(DB, name)
        if output is not None:
            return output
    return None


def clear_audio_and_video_outputs() -> None:
    for name in ["full_audio.mp3", "full_audio.wav", "full_audio.opus", "full_audio.pcm", "manifest.json", "final_video.mp4"]:
        storage.delete_output(DB, name)


def build_copywriting_cards() -> list[dict[str, Any]]:
    materials = storage.list_materials(DB)
    copywriting = storage.get_copywriting(DB) or {}
    cards = []
    for index, item in enumerate(materials, start=1):
        key = str(index)
        payload = copywriting.get(key, {})
        cards.append(
            {
                "key": key,
                "material_id": item["id"],
                "display_name": item["display_name"],
                "rank": payload.get("rank", item["forced_rank"] or ""),
                "texts": payload.get("text") or [""],
            }
        )
    return cards


def save_copywriting_from_form(form) -> None:
    materials = storage.list_materials(DB)
    payload: dict[str, Any] = {
        "0": {"text": [form.get("intro_text", "").strip() or "锐评开始"]},
        "-1": {"text": [form.get("outro_text", "").strip() or "以上就是本期全部内容。"]},
    }
    for index, item in enumerate(materials, start=1):
        key = str(index)
        rank = form.get(f"rank_{key}", "").strip()
        texts = [text.strip() for text in form.getlist(f"text_{key}") if text.strip()]
        payload[key] = {"rank": rank, "text": texts}
    projects = build_projects_for_validation(materials)
    main.validate_copywriting(payload, projects)
    storage.save_copywriting(DB, payload)


def build_projects_for_validation(materials: list[dict[str, Any]]) -> list[main.ProjectImage]:
    projects = []
    for index, item in enumerate(materials, start=1):
        projects.append(
            main.ProjectImage(
                order=index,
                key=str(index),
                name=item["filename"].rsplit(".", 1)[0],
                display_name=item["display_name"],
                forced_rank=item["forced_rank"],
                path=Path(item["filename"]),
            )
        )
    return projects


def start_job(stage: str, fn):
    global RUN_TOKEN
    with STATE_LOCK:
        RUN_TOKEN += 1
        run_token = RUN_TOKEN
        STATE.update(
            {
                "busy": True,
                "stage": stage,
                "progress": 0.0,
                "message": "",
                "error": "",
                "logs": [f"开始执行 {stage}"],
            }
        )
    thread = threading.Thread(target=run_job, args=(stage, fn, run_token), daemon=True)
    thread.start()
    return run_token


def common_page_context() -> dict[str, Any]:
    settings = storage.get_settings(DB)
    api_keys = read_api_keys()
    copywriting = storage.get_copywriting(DB) or {}
    final_video_exists = storage.get_output(DB, "final_video.mp4") is not None
    full_audio_exists = find_full_audio_output() is not None
    custom_voices = storage.list_custom_voices(DB)
    return {
        "state": STATE,
        "api_keys": api_keys,
        "settings": settings,
        "materials": storage.list_materials(DB),
        "siliconflow_voices": storage.SILICONFLOW_VOICES,
        "builtin_voices": storage.BUILTIN_VOICES,
        "custom_voices": custom_voices,
        "current_voice": settings.get("tts_voice", "builtin:Manbo"),
        "copywriting_cards": build_copywriting_cards(),
        "intro_text": (copywriting.get("0", {}).get("text", ["锐评开始"])[0] if copywriting else ""),
        "outro_text": (copywriting.get("-1", {}).get("text", ["以上就是本期全部内容。"])[0] if copywriting else ""),
        "final_video_exists": final_video_exists,
        "full_audio_exists": full_audio_exists,
    }


def serialize_state() -> dict[str, Any]:
    with STATE_LOCK:
        return dict(STATE)


def serialize_bootstrap() -> dict[str, Any]:
    settings = storage.get_settings(DB)
    api_keys = read_api_keys()
    copywriting = storage.get_copywriting(DB) or {}
    materials = storage.list_materials(DB)
    return {
        "state": serialize_state(),
        "api_keys": api_keys,
        "settings": settings,
        "materials": materials,
        "siliconflow_voices": storage.SILICONFLOW_VOICES,
        "builtin_voices": storage.BUILTIN_VOICES,
        "custom_voices": storage.list_custom_voices(DB),
        "current_voice": settings.get("tts_voice", "builtin:Manbo"),
        "copywriting_cards": build_copywriting_cards(),
        "intro_text": (copywriting.get("0", {}).get("text", ["锐评开始"])[0] if copywriting else ""),
        "outro_text": (copywriting.get("-1", {}).get("text", ["以上就是本期全部内容。"])[0] if copywriting else ""),
        "final_video_exists": storage.get_output(DB, "final_video.mp4") is not None,
        "full_audio_exists": find_full_audio_output() is not None,
    }


def json_ok(**payload: Any):
    return jsonify({"ok": True, **payload})


def render_step(template_name: str, active_step: str):
    context = common_page_context()
    context["active_step"] = active_step
    return render_template(template_name, **context)


def redirect_back(default_endpoint: str):
    return redirect(request.referrer or url_for(default_endpoint))


@app.route("/")
def index():
    return redirect(url_for("setup_page"))


@app.get("/setup")
def setup_page():
    return render_step("setup.html", "setup")


@app.get("/materials")
def materials_page():
    return render_step("materials.html", "materials")


@app.get("/copywriting")
def copywriting_page():
    return render_step("copywriting.html", "copywriting")


@app.get("/audio")
def audio_page():
    return render_step("audio.html", "audio")


@app.get("/video")
def video_page():
    return render_step("video.html", "video")


@app.get("/status")
def status_route():
    with STATE_LOCK:
        return jsonify(STATE)


@app.get("/api/bootstrap")
def api_bootstrap_route():
    return jsonify(serialize_bootstrap())


@app.get("/api/state")
def api_state_route():
    return jsonify(serialize_state())


@app.post("/api/setup")
def api_setup_route():
    payload = request.get_json(silent=True) or {}
    api_keys = payload.get("api_keys", {})
    settings = payload.get("settings", {})
    write_api_keys(
        str(api_keys.get("deepseek_api_key", "")),
        str(api_keys.get("siliconflow_api_key", "")),
    )
    storage.set_settings(
        DB,
        {
            "deepseek_model": settings.get("deepseek_model", "deepseek-chat"),
            "tts_model": settings.get("tts_model", "FunAudioLLM/CosyVoice2-0.5B"),
            "tts_voice": settings.get("tts_voice", "builtin:Manbo"),
            "tts_speed": settings.get("tts_speed", "1.0"),
            "tts_gain": settings.get("tts_gain", "0.0"),
            "tts_format": settings.get("tts_format", "mp3"),
            "tts_sample_rate": settings.get("tts_sample_rate", ""),
            "prompt_body": settings.get("prompt_body", "锐评AI从夯到拉"),
            "advanced_visible": "true" if str(settings.get("advanced_visible", "false")) == "true" else "false",
        },
    )
    update_state(message="准备配置已保存", error="", append_log="准备配置已保存")
    return json_ok(data=serialize_bootstrap())


@app.post("/api/materials/upload")
def api_upload_materials_route():
    files = []
    for uploaded_file in request.files.getlist("files"):
        if not uploaded_file.filename:
            continue
        files.append(
            {
                "filename": Path(uploaded_file.filename).name,
                "display_name": Path(uploaded_file.filename).stem,
                "mime_type": uploaded_file.mimetype,
                "content": uploaded_file.read(),
            }
        )
    if files:
        storage.add_materials(DB, files)
        update_state(message="素材已上传", error="", append_log=f"上传素材 {len(files)} 个")
    return json_ok(materials=storage.list_materials(DB))


@app.post("/api/materials/save")
def api_save_materials_route():
    payload = request.get_json(silent=True) or {}
    materials = payload.get("materials", [])
    storage.update_materials(DB, materials)
    update_state(message="素材信息已保存", error="", append_log="素材顺序和名称已更新")
    return json_ok(materials=storage.list_materials(DB))


@app.post("/api/materials/delete/<int:material_id>")
def api_delete_material_route(material_id: int):
    storage.delete_material(DB, material_id)
    update_state(message="素材已删除", error="", append_log=f"删除素材 {material_id}")
    return json_ok(materials=storage.list_materials(DB))


@app.post("/api/custom-voice")
def api_custom_voice_route():
    uploaded_file = request.files.get("voice_file")
    reference_text = request.form.get("reference_text", "").strip()
    reference_name = request.form.get("reference_name", "").strip() or "custom-reference"
    if uploaded_file is None or not uploaded_file.filename or not reference_text:
        return jsonify({"ok": False, "error": "请上传自定义音频并填写对应文本"}), 400
    voice_id = storage.save_custom_voice(
        DB,
        filename=Path(uploaded_file.filename).name,
        reference_name=reference_name,
        reference_text=reference_text,
        mime_type=uploaded_file.mimetype,
        content=uploaded_file.read(),
    )
    storage.set_settings(DB, {"tts_voice": f"custom:{voice_id}"})
    update_state(message="自定义音频已上传并启用", error="", append_log=f"启用自定义音频 {voice_id}")
    return json_ok(custom_voices=storage.list_custom_voices(DB), current_voice=f"custom:{voice_id}")


@app.post("/api/copywriting/save")
def api_save_copywriting_route():
    payload = request.get_json(silent=True) or {}
    intro_text = str(payload.get("intro_text", "")).strip() or "锐评开始"
    outro_text = str(payload.get("outro_text", "")).strip() or "以上就是本期全部内容。"
    cards = payload.get("cards", [])
    materials = storage.list_materials(DB)
    result: dict[str, Any] = {
        "0": {"text": [intro_text]},
        "-1": {"text": [outro_text]},
    }
    for index, item in enumerate(materials, start=1):
        key = str(index)
        card = next((card for card in cards if str(card.get("key")) == key), None)
        if card is None:
            continue
        texts = [str(text).strip() for text in card.get("texts", []) if str(text).strip()]
        result[key] = {"rank": str(card.get("rank", "")).strip(), "text": texts}
    projects = build_projects_for_validation(materials)
    main.validate_copywriting(result, projects)
    storage.save_copywriting(DB, result)
    update_state(message="图形化文案已保存", error="", append_log="图形化文案已保存")
    return json_ok(copywriting_cards=build_copywriting_cards())


@app.post("/api/jobs/<stage>")
def api_start_job_route(stage: str):
    if stage == "copywriting":
        storage.set_settings(DB, {"copywriting_json": ""})
        storage.clear_outputs(DB)
        start_job("copywriting", generate_copywriting_job)
    elif stage == "audio":
        clear_audio_and_video_outputs()
        start_job("audio", generate_audio_job)
    elif stage == "video":
        storage.delete_output(DB, "final_video.mp4")
        start_job("video", generate_video_only_job)
    else:
        return jsonify({"ok": False, "error": "未知任务类型"}), 400
    return json_ok(state=serialize_state())


@app.post("/api/reset-output")
def api_reset_output_route():
    storage.set_settings(DB, {"copywriting_json": ""})
    storage.clear_outputs(DB)
    update_state(message="已清空文案与输出结果", error="", progress=0.0, append_log="已清空文案与输出结果")
    return json_ok(data=serialize_bootstrap())


@app.post("/api/reset-project")
def api_reset_project_route():
    global RUN_TOKEN
    with STATE_LOCK:
        RUN_TOKEN += 1
    storage.clear_materials(DB)
    storage.set_settings(DB, {"copywriting_json": ""})
    storage.clear_outputs(DB)
    update_state(
        busy=False,
        stage="idle",
        progress=0.0,
        message="已清空当前视频素材、文案与生成结果，可直接开始下一个视频。",
        error="",
        logs=[],
        append_log="已开始新的空白项目，准备配置已保留",
    )
    return json_ok(data=serialize_bootstrap())


@app.post("/save-api-keys")
def save_api_keys_route():
    write_api_keys(request.form.get("deepseek_api_key", ""), request.form.get("siliconflow_api_key", ""))
    update_state(message="API Key 已保存", error="", append_log="API Key 已保存")
    return redirect_back("setup_page")


@app.post("/save-settings")
def save_settings_route():
    storage.set_settings(
        DB,
        {
            "deepseek_model": request.form.get("deepseek_model", "deepseek-chat"),
            "tts_model": request.form.get("tts_model", "FunAudioLLM/CosyVoice2-0.5B"),
            "tts_voice": request.form.get("tts_voice", "builtin:Manbo"),
            "tts_speed": request.form.get("tts_speed", "1.0"),
            "tts_gain": request.form.get("tts_gain", "0.0"),
            "tts_format": request.form.get("tts_format", "mp3"),
            "tts_sample_rate": request.form.get("tts_sample_rate", ""),
            "prompt_body": request.form.get("prompt_body", "锐评AI从夯到拉"),
            "advanced_visible": "true" if request.form.get("advanced_visible") == "true" else "false",
        },
    )
    update_state(message="业务配置已保存", error="", append_log="业务配置已保存")
    return redirect_back("setup_page")


@app.post("/upload-materials")
def upload_materials_route():
    files = []
    for uploaded_file in request.files.getlist("files"):
        if not uploaded_file.filename:
            continue
        files.append(
            {
                "filename": Path(uploaded_file.filename).name,
                "display_name": Path(uploaded_file.filename).stem,
                "mime_type": uploaded_file.mimetype,
                "content": uploaded_file.read(),
            }
        )
    if files:
        storage.add_materials(DB, files)
        update_state(message="素材已上传", error="", append_log=f"上传素材 {len(files)} 个")
    return redirect_back("materials_page")


@app.post("/save-materials")
def save_materials_route():
    order_payload = request.form.get("order_payload", "").strip()
    ids = [int(value) for value in order_payload.split(",") if value.strip()] or [int(value) for value in request.form.getlist("material_id")]
    materials = []
    for material_id in ids:
        materials.append(
            {
                "id": material_id,
                "display_name": request.form.get(f"display_name_{material_id}", "").strip(),
                "forced_rank": request.form.get(f"forced_rank_{material_id}", "").strip(),
            }
        )
    storage.update_materials(DB, materials)
    update_state(message="素材信息已保存", error="", append_log="素材顺序和名称已更新")
    return redirect_back("materials_page")


@app.post("/delete-material/<int:material_id>")
def delete_material_route(material_id: int):
    storage.delete_material(DB, material_id)
    update_state(message="素材已删除", error="", append_log=f"删除素材 {material_id}")
    return redirect_back("materials_page")


@app.route("/material/<int:material_id>")
def material_preview_route(material_id: int):
    material = storage.get_material(DB, material_id)
    return send_file(io.BytesIO(material["content"]), mimetype=material.get("mime_type") or "application/octet-stream")


@app.post("/upload-custom-voice")
def upload_custom_voice_route():
    uploaded_file = request.files.get("voice_file")
    reference_text = request.form.get("reference_text", "").strip()
    reference_name = request.form.get("reference_name", "").strip() or "custom-reference"
    if uploaded_file is None or not uploaded_file.filename or not reference_text:
        update_state(error="请上传自定义音频并填写对应文本", message="", append_log="自定义音频上传失败")
        return redirect_back("setup_page")
    voice_id = storage.save_custom_voice(
        DB,
        filename=Path(uploaded_file.filename).name,
        reference_name=reference_name,
        reference_text=reference_text,
        mime_type=uploaded_file.mimetype,
        content=uploaded_file.read(),
    )
    storage.set_settings(DB, {"tts_voice": f"custom:{voice_id}"})
    update_state(message="自定义音频已上传并启用", error="", append_log=f"启用自定义音频 {voice_id}")
    return redirect_back("setup_page")


@app.post("/generate-copywriting")
def generate_copywriting_route():
    storage.set_settings(DB, {"copywriting_json": ""})
    storage.clear_outputs(DB)
    start_job("copywriting", generate_copywriting_job)
    return redirect(url_for("copywriting_page"))


@app.post("/save-copywriting-form")
def save_copywriting_form_route():
    save_copywriting_from_form(request.form)
    update_state(message="图形化文案已保存", error="", append_log="图形化文案已保存")
    return redirect_back("copywriting_page")


@app.post("/generate-video")
def generate_video_route():
    storage.delete_output(DB, "final_video.mp4")
    start_job("video", generate_video_only_job)
    return redirect(url_for("video_page"))


@app.post("/generate-audio")
def generate_audio_route():
    clear_audio_and_video_outputs()
    start_job("audio", generate_audio_job)
    return redirect(url_for("audio_page"))


@app.route("/video/final")
def final_video_route():
    output = storage.get_output(DB, "final_video.mp4")
    if output is None:
        return Response(status=404)
    return send_file(io.BytesIO(output["content"]), mimetype=output["mime_type"], download_name="final_video.mp4")


@app.route("/audio/final")
def final_audio_route():
    output = find_full_audio_output()
    if output is None:
        return Response(status=404)
    return send_file(io.BytesIO(output["content"]), mimetype=output["mime_type"], download_name=output["name"])


@app.post("/reset-output")
def reset_output_route():
    storage.set_settings(DB, {"copywriting_json": ""})
    storage.clear_outputs(DB)
    update_state(message="已清空文案与输出结果", error="", progress=0.0, append_log="已清空文案与输出结果")
    return redirect_back("setup_page")


@app.post("/reset-project")
def reset_project_route():
    global RUN_TOKEN
    with STATE_LOCK:
        RUN_TOKEN += 1
    storage.clear_materials(DB)
    storage.set_settings(DB, {"copywriting_json": ""})
    storage.clear_outputs(DB)
    update_state(
        busy=False,
        stage="idle",
        progress=0.0,
        message="已清空当前视频素材、文案与生成结果，可直接开始下一个视频。",
        error="",
        logs=[],
        append_log="已开始新的空白项目，准备配置已保留",
    )
    return redirect(url_for("materials_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=False)
