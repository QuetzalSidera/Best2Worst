import json
import math
import re
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "config.toml"
MATERIAL_DIR = ROOT_DIR / "Material"
TEMPLATE_DIR = ROOT_DIR / "Template"
OUTPUT_DIR = ROOT_DIR / "Output"
COPYWRITING_PATH = OUTPUT_DIR / "Copywriting.json"
AUDIO_DIR = OUTPUT_DIR / "Audio"
VIDEO_DIR = OUTPUT_DIR / "Video"
SCENE_DIR = VIDEO_DIR / "Scenes"
TEMPLATE_IMAGE_PATH = TEMPLATE_DIR / "Blank.png"
DEFAULT_REFERENCE_AUDIO_PATH = TEMPLATE_DIR / "Audio" / "Manbo.mp3"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SCENE_PAUSE_SECONDS = 0.35
MAX_MOVE_DURATION_SECONDS = 0.7
MAX_TTS_WORKERS = 4


@dataclass
class TextApiConfig:
    api_key: str
    base_url: str
    model: str


@dataclass
class TtsApiConfig:
    api_key: str
    base_url: str
    model: str
    voice: str
    response_format: str
    speed: float
    gain: float
    sample_rate: int | None
    reference_audio: str | None
    reference_text: str | None
    reference_name: str | None


@dataclass
class VideoConfig:
    width: int
    height: int
    fps: int
    center_width: int
    center_height: int
    slot_width: int
    slot_height: int
    slot_columns: int
    slot_gap_x: int
    slot_gap_y: int
    slot_margin_x: int
    slot_margin_y: int


@dataclass
class ProjectSlot:
    x: int
    y: int
    width: int
    height: int


@dataclass
class ProjectImage:
    order: int
    key: str
    name: str
    display_name: str
    forced_rank: str | None
    path: Path


@dataclass
class AudioSegment:
    scene_index: int
    key: str
    text_index: int
    text: str
    audio_path: Path
    duration: float
    move_duration: float


@dataclass
class SceneStep:
    key: str
    project: ProjectImage | None
    mode: str
    text: str
    text_index: int


@dataclass
class PromptInjection:
    orders: list[dict[str, Any]]
    extra_prompt: str
    audio_speed: float | None
    reference_text: str | None
    reference_name: str | None
    tts_model: str | None


ProgressCallback = Callable[[float, str], None]


def load_config() -> tuple[TextApiConfig, TtsApiConfig, VideoConfig]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

    with CONFIG_PATH.open("rb") as file_handle:
        raw_config = tomllib.load(file_handle)

    text_section = raw_config.get("text_api", {})
    tts_section = raw_config.get("tts_api", {})
    video_section = raw_config.get("video", {})

    text_config = TextApiConfig(
        api_key=require_value(text_section, "api_key", "text_api.api_key"),
        base_url=text_section.get("base_url", "https://api.deepseek.com"),
        model=text_section.get("model", "deepseek-chat"),
    )
    reference_audio = tts_section.get("reference_audio")
    if reference_audio is None and DEFAULT_REFERENCE_AUDIO_PATH.exists():
        reference_audio = str(DEFAULT_REFERENCE_AUDIO_PATH)

    tts_config = TtsApiConfig(
        api_key=require_value(tts_section, "api_key", "tts_api.api_key"),
        base_url=tts_section.get("base_url", "https://api.siliconflow.cn/v1"),
        model=tts_section.get("model", "FunAudioLLM/CosyVoice2-0.5B"),
        voice=tts_section.get("voice", "FunAudioLLM/CosyVoice2-0.5B:diana"),
        response_format=tts_section.get("response_format", "mp3"),
        speed=float(tts_section.get("speed", 1.0)),
        gain=float(tts_section.get("gain", 0.0)),
        sample_rate=tts_section.get("sample_rate"),
        reference_audio=str(reference_audio) if reference_audio else None,
        reference_text=tts_section.get("reference_text"),
        reference_name=tts_section.get("reference_name", "template-manbo"),
    )
    video_config = VideoConfig(
        width=int(video_section.get("width", 1080)),
        height=int(video_section.get("height", 1920)),
        fps=int(video_section.get("fps", 25)),
        center_width=int(video_section.get("center_width", 760)),
        center_height=int(video_section.get("center_height", 760)),
        slot_width=int(video_section.get("slot_width", 220)),
        slot_height=int(video_section.get("slot_height", 220)),
        slot_columns=int(video_section.get("slot_columns", 3)),
        slot_gap_x=int(video_section.get("slot_gap_x", 36)),
        slot_gap_y=int(video_section.get("slot_gap_y", 36)),
        slot_margin_x=int(video_section.get("slot_margin_x", 80)),
        slot_margin_y=int(video_section.get("slot_margin_y", 120)),
    )
    return text_config, tts_config, video_config


def require_value(section: dict[str, Any], key: str, label: str) -> str:
    value = section.get(key)
    if not value:
        raise ValueError(f"Missing required config value: {label}")
    return str(value)


def ensure_directories() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        result = subprocess.run(["which", tool], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"{tool} is required but was not found in PATH.")


def load_system_prompt() -> str:
    system_prompt_path = TEMPLATE_DIR / "System.md"
    if not system_prompt_path.exists():
        fallback_path = TEMPLATE_DIR / "system.md"
        system_prompt_path = fallback_path
    if not system_prompt_path.exists():
        raise FileNotFoundError("System prompt file not found in Template/System.md")
    return system_prompt_path.read_text(encoding="utf-8")


def parse_prompt_file() -> PromptInjection:
    prompt_path = MATERIAL_DIR / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not match:
        raise ValueError("Material/prompt.md must begin with a YAML front matter block.")

    yaml_block = match.group(1)
    extra_prompt = match.group(2).strip()
    payload = yaml.safe_load(yaml_block) or {}
    orders = payload.get("orders")
    if not isinstance(orders, list) or not orders:
        raise ValueError("Material/prompt.md must define a non-empty orders list.")
    audio_section = payload.get("audio", {})
    if audio_section is None:
        audio_section = {}
    if not isinstance(audio_section, dict):
        raise ValueError("Material/prompt.md audio section must be a mapping if provided.")
    audio_speed = audio_section.get("speed")
    if audio_speed is not None:
        audio_speed = float(audio_speed)
    reference_text = audio_section.get("reference_text")
    reference_name = audio_section.get("reference_name")
    tts_model = audio_section.get("model")
    return PromptInjection(
        orders=orders,
        extra_prompt=extra_prompt,
        audio_speed=audio_speed,
        reference_text=str(reference_text) if reference_text else None,
        reference_name=str(reference_name) if reference_name else None,
        tts_model=str(tts_model) if tts_model else None,
    )


def collect_project_images(order_entries: list[dict[str, Any]]) -> list[ProjectImage]:
    material_files = {
        path.name: path
        for path in MATERIAL_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    }
    if not material_files:
        raise FileNotFoundError(f"No images found in {MATERIAL_DIR}")

    projects: list[ProjectImage] = []
    seen_keys: set[str] = set()
    for entry in sorted(order_entries, key=lambda item: int(item["order"])):
        file_name = entry.get("file")
        order = int(entry.get("order"))
        if file_name not in material_files:
            raise FileNotFoundError(f"Image referenced in prompt.md not found: {file_name}")
        key = str(order)
        if key in seen_keys:
            raise ValueError(f"Duplicate order found in prompt.md: {order}")
        seen_keys.add(key)
        image_path = material_files[file_name]
        forced_rank = entry.get("rank")
        if forced_rank is not None:
            forced_rank = str(forced_rank)
        projects.append(
            ProjectImage(
                order=order,
                key=key,
                name=image_path.stem,
                display_name=str(entry.get("name") or image_path.stem),
                forced_rank=forced_rank,
                path=image_path,
            )
        )
    return projects


def build_user_prompt(projects: list[ProjectImage], extra_prompt: str) -> str:
    ordered_lines = "\n".join(
        build_project_prompt_line(project)
        for project in projects
    )
    return (
        "请严格按照给定顺序生成 JSON。\n"
        "文案必须像短视频锐评口播，不要写成说明书、评测报告或客服回复。\n"
        "项目名称只能使用给定的显示名称，严禁在文案里使用图片文件名或文件名变体。\n"
        "输出必须是纯 JSON，对象 key 包含 0、各项目 order、-1。\n"
        "0 和 -1 的值结构固定为 {\"text\": [\"...\"]}。\n"
        "每个项目项的值结构固定为 {\"rank\": \"夯|顶级|人上人|NPC|拉完了\", \"text\": [\"...\", \"...\"]}。\n"
        "每个项目的 text 必须是非空字符串数组，句子数量不固定，按内容需要生成即可。\n"
        "rank 必须显式给出，供视频合成时定位图片，不允许省略，不允许输出其它等级词。\n"
        "最后一句必须明确说出评级，但不要每个项目都复读同一种句式，尤其不要机械重复“所以这个只能给到……”。\n"
        "如果某个项目给出了指定 rank，你必须输出该 rank，不要改动。\n"
        "0 表示开场，-1 表示结尾。\n\n"
        f"项目顺序：\n{ordered_lines}\n\n"
        f"补充提示词：\n{extra_prompt or '无'}"
    )


def build_project_prompt_line(project: ProjectImage) -> str:
    line = f"{project.key}. 显示名称={project.display_name}"
    if project.forced_rank:
        line += f"，指定评级={project.forced_rank}"
    return line


def call_deepseek_chat(config: TextApiConfig, system_prompt: str, user_prompt: str) -> str:
    import requests

    response = requests.post(
        f"{config.base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.8,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"].strip()


def extract_json_block(content: str) -> dict[str, Any]:
    fenced_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced_match:
        content = fenced_match.group(1)

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model output did not contain a valid JSON object.")
    return json.loads(content[start : end + 1])


def validate_copywriting(copywriting: dict[str, Any], projects: list[ProjectImage]) -> None:
    expected_keys = {"0", "-1"} | {project.key for project in projects}
    missing_keys = expected_keys - set(copywriting.keys())
    if missing_keys:
        raise ValueError(f"Copywriting JSON is missing keys: {sorted(missing_keys)}")

    allowed_ranks = {"夯", "顶级", "人上人", "NPC", "拉完了"}
    for key in expected_keys:
        value = copywriting.get(key)
        if not isinstance(value, dict) or not isinstance(value.get("text"), list) or not value["text"]:
            raise ValueError(f'Copywriting JSON key "{key}" must contain a non-empty text array.')
        if key in {"0", "-1"}:
            continue
        rank = value.get("rank")
        if rank not in allowed_ranks:
            raise ValueError(f'Copywriting JSON key "{key}" must contain a valid rank field.')
        project = next(project for project in projects if project.key == key)
        if project.forced_rank and rank != project.forced_rank:
            raise ValueError(f'Copywriting JSON key "{key}" rank must be "{project.forced_rank}".')


def generate_copywriting(text_config: TextApiConfig, projects: list[ProjectImage]) -> dict[str, Any]:
    system_prompt = load_system_prompt()
    injection = parse_prompt_file()
    expected_projects = collect_project_images(injection.orders)
    if [project.path.name for project in projects] != [project.path.name for project in expected_projects]:
        raise ValueError("Project images and prompt order are out of sync.")

    user_prompt = build_user_prompt(projects, injection.extra_prompt)
    model_output = call_deepseek_chat(text_config, system_prompt, user_prompt)
    copywriting = extract_json_block(model_output)
    validate_copywriting(copywriting, projects)
    return copywriting


def save_copywriting(copywriting: dict[str, Any]) -> None:
    COPYWRITING_PATH.write_text(json.dumps(copywriting, ensure_ascii=False, indent=2), encoding="utf-8")


def load_copywriting_from_disk(projects: list[ProjectImage]) -> dict[str, Any]:
    if not COPYWRITING_PATH.exists():
        raise FileNotFoundError(f"Copywriting file not found: {COPYWRITING_PATH}")
    copywriting = json.loads(COPYWRITING_PATH.read_text(encoding="utf-8"))
    validate_copywriting(copywriting, projects)
    return copywriting


def wait_for_approval() -> None:
    print(f"文案已生成，请检查并编辑 {COPYWRITING_PATH}")
    input("确认无误后按回车继续生成配音与视频...")


def upload_siliconflow_voice(config: TtsApiConfig) -> str:
    import requests

    if not config.reference_audio:
        return config.voice
    reference_text = config.reference_text
    if not reference_text or not config.reference_name:
        raise ValueError(
            "reference_text and reference_name are required when reference_audio is configured. "
            "Use config.toml or Material/prompt.md audio.reference_text to provide the exact transcript of the reference audio."
        )

    audio_path = Path(config.reference_audio).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Reference audio file not found: {audio_path}")

    with audio_path.open("rb") as file_handle:
        response = requests.post(
            f"{config.base_url.rstrip('/')}/uploads/audio/voice",
            headers={"Authorization": f"Bearer {config.api_key}"},
            files={"file": (audio_path.name, file_handle)},
            data={
                "model": config.model,
                "customName": config.reference_name,
                "text": reference_text,
            },
            timeout=300,
        )
    response.raise_for_status()
    payload = response.json()
    return payload["uri"]


def synthesize_speech(config: TtsApiConfig, text: str, output_path: Path, voice: str) -> None:
    import requests

    payload: dict[str, Any] = {
        "model": config.model,
        "voice": voice,
        "input": text,
        "response_format": config.response_format,
        "speed": config.speed,
        "gain": config.gain,
    }
    if config.sample_rate is not None:
        payload["sample_rate"] = config.sample_rate

    last_error: str | None = None
    for _ in range(3):
        response = requests.post(
            f"{config.base_url.rstrip('/')}/audio/speech",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
        if response.status_code >= 400:
            body = response.text[:500]
            last_error = f"TTS API request failed with status {response.status_code}: {body}"
            continue

        content_type = response.headers.get("Content-Type", "")
        if not response.content:
            last_error = "TTS API returned an empty body."
            continue
        if "application/json" in content_type:
            last_error = f"TTS API returned JSON instead of audio: {response.text[:500]}"
            continue

        output_path.write_bytes(response.content)
        return

    raise RuntimeError(last_error or "TTS API did not return a valid audio file.")


def audio_codec_args(response_format: str, sample_rate: int) -> list[str]:
    if response_format == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", str(sample_rate), "-ac", "1"]
    if response_format == "opus":
        return ["-c:a", "libopus", "-b:a", "96k", "-ar", str(sample_rate), "-ac", "1"]
    if response_format == "wav":
        return ["-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1"]
    if response_format == "pcm":
        return ["-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1"]
    raise ValueError(f"Unsupported response format: {response_format}")


def normalize_and_pad_audio(
    input_path: Path,
    output_path: Path,
    response_format: str,
    sample_rate: int,
    pause_duration: float,
) -> None:
    if pause_duration > 0:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-f",
            "lavfi",
            "-t",
            f"{pause_duration:.3f}",
            "-i",
            f"anullsrc=r={sample_rate}:cl=mono",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1[aout]",
            "-map",
            "[aout]",
            *audio_codec_args(response_format, sample_rate),
            str(output_path),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            *audio_codec_args(response_format, sample_rate),
            str(output_path),
        ]
    run_ffmpeg(command)


def get_media_duration(media_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "ffprobe failed"
        raise RuntimeError(f"Could not read media duration for {media_path.name}: {stderr}")
    return float(result.stdout.strip())


def get_image_dimensions(image_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(image_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)


def build_scene_steps(copywriting: dict[str, Any], projects: list[ProjectImage]) -> list[SceneStep]:
    steps: list[SceneStep] = []
    intro_text = " ".join(text.strip() for text in copywriting["0"]["text"] if text.strip())
    if intro_text:
        steps.append(SceneStep(key="0", project=None, mode="blank", text=intro_text, text_index=0))

    for project in projects:
        texts = [text.strip() for text in copywriting[project.key]["text"] if text.strip()]
        if len(texts) > 1:
            prefix_text = " ".join(texts[:-1]).strip()
            if prefix_text:
                steps.append(SceneStep(key=project.key, project=project, mode="center", text=prefix_text, text_index=0))
        steps.append(
            SceneStep(
                key=project.key,
                project=project,
                mode="move",
                text=texts[-1],
                text_index=len(texts) - 1,
            )
        )

    outro_text = " ".join(text.strip() for text in copywriting["-1"]["text"] if text.strip())
    if outro_text:
        steps.append(SceneStep(key="-1", project=None, mode="blank", text=outro_text, text_index=0))
    return steps


def synthesize_single_segment(
    scene_index: int,
    step: SceneStep,
    total_segments: int,
    tts_config: TtsApiConfig,
    voice: str,
    extension: str,
    sample_rate: int,
) -> AudioSegment:
    raw_audio_path = AUDIO_DIR / f"{scene_index:03d}_raw.{extension}"
    audio_path = AUDIO_DIR / f"{scene_index:03d}.{extension}"
    synthesize_speech(tts_config, step.text, raw_audio_path, voice)
    move_duration = get_media_duration(raw_audio_path)
    pause_duration = SCENE_PAUSE_SECONDS if scene_index < total_segments - 1 else 0.0
    normalize_and_pad_audio(
        input_path=raw_audio_path,
        output_path=audio_path,
        response_format=tts_config.response_format,
        sample_rate=sample_rate,
        pause_duration=pause_duration,
    )
    raw_audio_path.unlink(missing_ok=True)
    duration = get_media_duration(audio_path)
    return AudioSegment(
        scene_index=scene_index,
        key=step.key,
        text_index=step.text_index,
        text=step.text,
        audio_path=audio_path,
        duration=duration,
        move_duration=move_duration,
    )


def synthesize_audio_segments(
    copywriting: dict[str, Any],
    tts_config: TtsApiConfig,
    projects: list[ProjectImage],
    progress_callback: ProgressCallback | None = None,
) -> list[AudioSegment]:
    voice = upload_siliconflow_voice(tts_config)
    scene_steps = build_scene_steps(copywriting, projects)
    extension = "wav" if tts_config.response_format == "pcm" else tts_config.response_format
    sample_rate = int(tts_config.sample_rate or 24000)
    total_segments = len(scene_steps)
    if total_segments == 0:
        return []

    completed_count = 0
    segments_by_index: dict[int, AudioSegment] = {}
    worker_count = min(MAX_TTS_WORKERS, total_segments)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                synthesize_single_segment,
                scene_index,
                step,
                total_segments,
                tts_config,
                voice,
                extension,
                sample_rate,
            ): scene_index
            for scene_index, step in enumerate(scene_steps)
        }
        for future in as_completed(future_map):
            segment = future.result()
            segments_by_index[segment.scene_index] = segment
            completed_count += 1
            progress = 10 + (completed_count / total_segments) * 45
            emit_progress(progress_callback, progress, f"音频生成中 {completed_count}/{total_segments}")

    return [segments_by_index[index] for index in range(total_segments)]


def audio_extension(response_format: str) -> str:
    return "wav" if response_format == "pcm" else response_format


def build_project_grades(copywriting: dict[str, Any], projects: list[ProjectImage]) -> dict[str, str]:
    return {project.key: str(copywriting[project.key]["rank"]) for project in projects}


def compute_project_slots(video_config: VideoConfig, projects: list[ProjectImage], project_grades: dict[str, str]) -> dict[str, ProjectSlot]:
    grade_order = ["夯", "顶级", "人上人", "NPC", "拉完了"]
    label_width = round(video_config.width * 252 / 1422)
    board_x = label_width
    board_width = video_config.width - board_x
    row_height = video_config.height / len(grade_order)
    left_padding = round(board_width * 0.03)
    right_padding = left_padding
    horizontal_gap = round(board_width * 0.015)
    vertical_padding = round(row_height * 0.12)

    slots: dict[str, ProjectSlot] = {}
    for row_index, grade in enumerate(grade_order):
        row_projects = [project for project in projects if project_grades[project.key] == grade]
        if not row_projects:
            continue

        available_width = board_width - left_padding - right_padding
        row_slot_width = min(
            int(row_height - 2 * vertical_padding),
            int((available_width - horizontal_gap * (len(row_projects) - 1)) / len(row_projects)),
        )
        row_slot_width = max(row_slot_width, 80)
        row_slot_height = row_slot_width
        total_width = row_slot_width * len(row_projects) + horizontal_gap * (len(row_projects) - 1)
        start_x = board_x + max(left_padding, (board_width - total_width) // 2)
        y = int(row_index * row_height + (row_height - row_slot_height) / 2)

        for column_index, project in enumerate(row_projects):
            x = start_x + column_index * (row_slot_width + horizontal_gap)
            slots[project.key] = ProjectSlot(x=x, y=y, width=row_slot_width, height=row_slot_height)
    return slots


def center_slot(video_config: VideoConfig) -> ProjectSlot:
    label_width = round(video_config.width * 252 / 1422)
    board_width = video_config.width - label_width
    center_width = int(min(board_width * 0.42, video_config.height * 0.42))
    center_height = center_width
    x = label_width + (board_width - center_width) // 2
    y = (video_config.height - center_height) // 2
    return ProjectSlot(x=x, y=y, width=center_width, height=center_height)


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg command failed")


def render_scene(
    output_path: Path,
    duration: float,
    video_config: VideoConfig,
    retained_projects: list[ProjectImage],
    current_project: ProjectImage | None,
    current_mode: str,
    project_slots: dict[str, ProjectSlot],
    move_duration: float | None = None,
) -> None:
    inputs = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(TEMPLATE_IMAGE_PATH),
    ]

    filter_parts = [f"[0:v]scale={video_config.width}:{video_config.height}[base]"]
    current_stream = "[base]"
    input_index = 1

    for project in retained_projects:
        slot = project_slots[project.key]
        inputs.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(project.path)])
        filter_parts.append(
            f"[{input_index}:v]scale={slot.width}:{slot.height}:"
            f"force_original_aspect_ratio=decrease[slot{input_index}]"
        )
        filter_parts.append(
            f"{current_stream}[slot{input_index}]overlay={slot.x}:{slot.y}:format=auto[base{input_index}]"
        )
        current_stream = f"[base{input_index}]"
        input_index += 1

    if current_project is not None:
        slot = project_slots[current_project.key]
        focus_slot = center_slot(video_config)
        inputs.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(current_project.path)])
        if current_mode == "center":
            filter_parts.append(
                f"[{input_index}:v]scale={focus_slot.width}:{focus_slot.height}:"
                "force_original_aspect_ratio=decrease[current]"
            )
            center_x = focus_slot.x
            center_y = focus_slot.y
            overlay = f"{current_stream}[current]overlay={center_x}:{center_y}:format=auto[outv]"
        elif current_mode == "move":
            filter_parts.append(
                f"[{input_index}:v]scale={slot.width}:{slot.height}:"
                "force_original_aspect_ratio=decrease[current]"
            )
            center_x = focus_slot.x + (focus_slot.width - slot.width) // 2
            center_y = focus_slot.y + (focus_slot.height - slot.height) // 2
            effective_move_duration = min(max(move_duration or duration, 0.001), MAX_MOVE_DURATION_SECONDS)
            overlay = (
                f"{current_stream}[current]overlay="
                f"x='if(lte(t,{effective_move_duration:.3f}),{center_x}+({slot.x}-{center_x})*(t/{effective_move_duration:.3f}),{slot.x})':"
                f"y='if(lte(t,{effective_move_duration:.3f}),{center_y}+({slot.y}-{center_y})*(t/{effective_move_duration:.3f}),{slot.y})':"
                "format=auto[outv]"
            )
        else:
            raise ValueError(f"Unsupported current mode: {current_mode}")
        filter_parts.append(overlay)
        current_stream = "[outv]"

    if current_project is None:
        filter_parts.append(f"{current_stream}format=yuv420p[outv]")
        current_stream = "[outv]"
    else:
        filter_parts.append("[outv]format=yuv420p[outv2]")
        current_stream = "[outv2]"

    command = (
        inputs
        + [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            current_stream,
            "-r",
            str(video_config.fps),
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    run_ffmpeg(command)


def render_video_scenes(
    segments: list[AudioSegment],
    copywriting: dict[str, Any],
    projects: list[ProjectImage],
    video_config: VideoConfig,
    progress_callback: ProgressCallback | None = None,
) -> list[Path]:
    project_grades = build_project_grades(copywriting, projects)
    project_slots = compute_project_slots(video_config, projects, project_grades)
    scene_steps = build_scene_steps(copywriting, projects)
    if len(scene_steps) != len(segments):
        raise ValueError("Scene plan and audio segments count do not match.")

    rendered_paths: list[Path] = []
    retained_projects: list[ProjectImage] = []
    seen_project_keys: set[str] = set()

    total_segments = len(segments)
    for rendered_count, (segment, step) in enumerate(zip(segments, scene_steps), start=1):
        scene_path = SCENE_DIR / f"{segment.scene_index:03d}.mp4"
        render_scene(
            output_path=scene_path,
            duration=segment.duration,
            video_config=video_config,
            retained_projects=retained_projects,
            current_project=step.project,
            current_mode=step.mode if step.mode != "blank" else "center",
            project_slots=project_slots,
            move_duration=segment.move_duration,
        )
        rendered_paths.append(scene_path)
        progress = 55 + (rendered_count / total_segments) * 35
        emit_progress(progress_callback, progress, f"视频分镜生成中 {rendered_count}/{total_segments}")

        if step.key not in {"0", "-1"} and step.project is not None and step.mode == "move" and step.key not in seen_project_keys:
            retained_projects.append(step.project)
            seen_project_keys.add(step.key)
    return rendered_paths


def concat_media(paths: list[Path], concat_file: Path, output_path: Path) -> None:
    concat_content = "\n".join(f"file '{path.as_posix()}'" for path in paths)
    concat_file.write_text(concat_content, encoding="utf-8")
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    run_ffmpeg(command)


def mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    run_ffmpeg(command)


def write_manifest(segments: list[AudioSegment], projects: list[ProjectImage]) -> None:
    manifest = {
        "projects": [
            {
                "order": project.order,
                "key": project.key,
                "name": project.name,
                "display_name": project.display_name,
                "forced_rank": project.forced_rank,
                "file": project.path.name,
            }
            for project in projects
        ],
        "audio_segments": [
            {
                "scene_index": segment.scene_index,
                "key": segment.key,
                "text_index": segment.text_index,
                "text": segment.text,
                "audio_file": segment.audio_path.name,
                "duration": segment.duration,
                "move_duration": segment.move_duration,
            }
            for segment in segments
        ],
    }
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build_manifest_payload(segments: list[AudioSegment], projects: list[ProjectImage]) -> dict[str, Any]:
    return {
        "projects": [
            {
                "order": project.order,
                "key": project.key,
                "name": project.name,
                "display_name": project.display_name,
                "forced_rank": project.forced_rank,
                "file": project.path.name,
            }
            for project in projects
        ],
        "audio_segments": [
            {
                "scene_index": segment.scene_index,
                "key": segment.key,
                "text_index": segment.text_index,
                "text": segment.text,
                "audio_file": segment.audio_path.name,
                "duration": segment.duration,
                "move_duration": segment.move_duration,
            }
            for segment in segments
        ],
    }


def segments_from_manifest(manifest: dict[str, Any]) -> list[AudioSegment]:
    return [
        AudioSegment(
            scene_index=int(item["scene_index"]),
            key=str(item["key"]),
            text_index=int(item["text_index"]),
            text=str(item["text"]),
            audio_path=AUDIO_DIR / str(item["audio_file"]),
            duration=float(item["duration"]),
            move_duration=float(item.get("move_duration", item["duration"])),
        )
        for item in manifest["audio_segments"]
    ]


def prepare_projects() -> list[ProjectImage]:
    injection = parse_prompt_file()
    return collect_project_images(injection.orders)


def apply_prompt_injection_to_tts_config(tts_config: TtsApiConfig, injection: PromptInjection) -> TtsApiConfig:
    if injection.audio_speed is not None:
        tts_config.speed = injection.audio_speed
    if injection.reference_text:
        tts_config.reference_text = injection.reference_text
    if injection.reference_name:
        tts_config.reference_name = injection.reference_name
    if injection.tts_model:
        tts_config.model = injection.tts_model
    return tts_config


def emit_progress(progress_callback: ProgressCallback | None, progress: float, message: str) -> None:
    if progress_callback is not None:
        progress_callback(progress, message)


def load_runtime_context() -> tuple[TextApiConfig, TtsApiConfig, VideoConfig, PromptInjection, list[ProjectImage]]:
    ensure_directories()
    ensure_ffmpeg()
    text_config, tts_config, video_config = load_config()
    injection = parse_prompt_file()
    tts_config = apply_prompt_injection_to_tts_config(tts_config, injection)
    video_config.width, video_config.height = get_image_dimensions(TEMPLATE_IMAGE_PATH)
    projects = collect_project_images(injection.orders)
    return text_config, tts_config, video_config, injection, projects


def compose_prompt_markdown(
    projects: list[dict[str, Any]],
    prompt_body: str,
    audio_options: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "orders": [
            {
                key: value
                for key, value in {
                    "file": project["file"],
                    "order": project["order"],
                    "name": project.get("name"),
                    "rank": project.get("rank"),
                }.items()
                if value not in (None, "")
            }
            for project in projects
        ]
    }
    if audio_options:
        payload["audio"] = {key: value for key, value in audio_options.items() if value not in (None, "")}
    yaml_block = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_block}\n---\n\n{prompt_body.strip()}\n"


@contextmanager
def use_workspace(config_path: Path, material_dir: Path, output_dir: Path, template_dir: Path | None = None):
    global CONFIG_PATH, MATERIAL_DIR, TEMPLATE_DIR, OUTPUT_DIR, COPYWRITING_PATH, AUDIO_DIR, VIDEO_DIR, SCENE_DIR, TEMPLATE_IMAGE_PATH, DEFAULT_REFERENCE_AUDIO_PATH

    old_values = (
        CONFIG_PATH,
        MATERIAL_DIR,
        TEMPLATE_DIR,
        OUTPUT_DIR,
        COPYWRITING_PATH,
        AUDIO_DIR,
        VIDEO_DIR,
        SCENE_DIR,
        TEMPLATE_IMAGE_PATH,
        DEFAULT_REFERENCE_AUDIO_PATH,
    )
    try:
        CONFIG_PATH = config_path
        MATERIAL_DIR = material_dir
        TEMPLATE_DIR = template_dir or TEMPLATE_DIR
        OUTPUT_DIR = output_dir
        COPYWRITING_PATH = OUTPUT_DIR / "Copywriting.json"
        AUDIO_DIR = OUTPUT_DIR / "Audio"
        VIDEO_DIR = OUTPUT_DIR / "Video"
        SCENE_DIR = VIDEO_DIR / "Scenes"
        TEMPLATE_IMAGE_PATH = TEMPLATE_DIR / "Blank.png"
        DEFAULT_REFERENCE_AUDIO_PATH = TEMPLATE_DIR / "Audio" / "Manbo.mp3"
        yield
    finally:
        (
            CONFIG_PATH,
            MATERIAL_DIR,
            TEMPLATE_DIR,
            OUTPUT_DIR,
            COPYWRITING_PATH,
            AUDIO_DIR,
            VIDEO_DIR,
            SCENE_DIR,
            TEMPLATE_IMAGE_PATH,
            DEFAULT_REFERENCE_AUDIO_PATH,
        ) = old_values


def generate_copywriting_step(progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    emit_progress(progress_callback, 5, "检查配置与素材")
    text_config, _, _, _, projects = load_runtime_context()
    emit_progress(progress_callback, 20, "调用文本模型生成文案")
    copywriting = generate_copywriting(text_config, projects)
    save_copywriting(copywriting)
    emit_progress(progress_callback, 100, "文案已生成")
    return copywriting


def finalize_video_step(progress_callback: ProgressCallback | None = None) -> Path:
    emit_progress(progress_callback, 5, "检查文案与运行环境")
    _, tts_config, video_config, _, projects = load_runtime_context()
    approved_copywriting = load_copywriting_from_disk(projects)
    emit_progress(progress_callback, 10, "开始生成分段音频")
    segments = synthesize_audio_segments(approved_copywriting, tts_config, projects, progress_callback)
    emit_progress(progress_callback, 55, "开始生成视频分镜")
    scene_paths = render_video_scenes(segments, approved_copywriting, projects, video_config, progress_callback)
    full_audio_path = AUDIO_DIR / f"full_audio.{audio_extension(tts_config.response_format)}"
    emit_progress(progress_callback, 92, "拼接完整音频与视频")
    concat_media([segment.audio_path for segment in segments], AUDIO_DIR / "audio_concat.txt", full_audio_path)
    concat_media(scene_paths, VIDEO_DIR / "video_concat.txt", VIDEO_DIR / "silent_video.mp4")
    mux_audio(VIDEO_DIR / "silent_video.mp4", full_audio_path, VIDEO_DIR / "final_video.mp4")
    write_manifest(segments, projects)
    emit_progress(progress_callback, 100, "最终视频已生成")
    return VIDEO_DIR / "final_video.mp4"


def generate_audio_step(progress_callback: ProgressCallback | None = None) -> tuple[Path, dict[str, Any]]:
    emit_progress(progress_callback, 5, "检查文案与运行环境")
    _, tts_config, _, _, projects = load_runtime_context()
    approved_copywriting = load_copywriting_from_disk(projects)
    emit_progress(progress_callback, 10, "开始生成分段音频")
    segments = synthesize_audio_segments(approved_copywriting, tts_config, projects, progress_callback)
    full_audio_path = AUDIO_DIR / f"full_audio.{audio_extension(tts_config.response_format)}"
    emit_progress(progress_callback, 92, "拼接完整音频")
    concat_media([segment.audio_path for segment in segments], AUDIO_DIR / "audio_concat.txt", full_audio_path)
    manifest = build_manifest_payload(segments, projects)
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    emit_progress(progress_callback, 100, "完整音频已生成")
    return full_audio_path, manifest


def generate_video_from_manifest_step(manifest: dict[str, Any], progress_callback: ProgressCallback | None = None) -> Path:
    emit_progress(progress_callback, 5, "检查文案、音频与运行环境")
    _, tts_config, video_config, _, projects = load_runtime_context()
    approved_copywriting = load_copywriting_from_disk(projects)
    segments = segments_from_manifest(manifest)
    emit_progress(progress_callback, 20, "开始生成视频分镜")
    scene_paths = render_video_scenes(segments, approved_copywriting, projects, video_config, progress_callback)
    full_audio_path = AUDIO_DIR / f"full_audio.{audio_extension(tts_config.response_format)}"
    if not full_audio_path.exists():
        raise FileNotFoundError(f"Full audio not found: {full_audio_path}")
    emit_progress(progress_callback, 92, "拼接最终视频")
    concat_media(scene_paths, VIDEO_DIR / "video_concat.txt", VIDEO_DIR / "silent_video.mp4")
    mux_audio(VIDEO_DIR / "silent_video.mp4", full_audio_path, VIDEO_DIR / "final_video.mp4")
    emit_progress(progress_callback, 100, "最终视频已生成")
    return VIDEO_DIR / "final_video.mp4"


def main() -> None:
    generate_copywriting_step()
    wait_for_approval()
    finalize_video_step()
    print(f"文案输出: {COPYWRITING_PATH}")
    print(f"音频输出目录: {AUDIO_DIR}")
    print(f"视频输出目录: {VIDEO_DIR}")


if __name__ == "__main__":
    main()
