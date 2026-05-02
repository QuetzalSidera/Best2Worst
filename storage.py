import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  display_name TEXT NOT NULL,
  forced_rank TEXT,
  sort_order INTEGER NOT NULL,
  mime_type TEXT,
  content BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_voices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  reference_name TEXT NOT NULL,
  reference_text TEXT NOT NULL,
  mime_type TEXT,
  content BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS outputs (
  name TEXT PRIMARY KEY,
  mime_type TEXT,
  content BLOB NOT NULL
);
"""


DEFAULT_SETTINGS = {
    "deepseek_model": "deepseek-chat",
    "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
    "tts_voice": "builtin:Manbo",
    "tts_speed": "1.0",
    "tts_gain": "0.0",
    "tts_format": "mp3",
    "tts_sample_rate": "",
    "prompt_body": "锐评AI从夯到拉",
    "advanced_visible": "false",
}


BUILTIN_VOICES = {
    "builtin:Manbo": {
        "label": "曼波 Manbo",
        "filename": "Manbo.mp3",
        "reference_name": "template-manbo",
        "reference_text": "这是一段用于AI语音训练的示例音频。请以自然、清晰的语速朗读这句话，保持发音准确、语调平稳。朗读时尽量减少停顿与杂音，使整体语音流畅连贯。",
    },
    "builtin:MacArthur": {
        "label": "麦克阿瑟 MacArthur",
        "filename": "MacArthur.mp3",
        "reference_name": "template-macarthur",
        "reference_text": "这是一段用于AI语音训练的示例音频。请以自然、清晰的语速朗读这句话，保持发音准确、语调平稳。朗读时尽量减少停顿与杂音，使整体语音流畅连贯。",
    },
    "builtin:DingZhen": {
        "label": "丁真 DingZhen",
        "filename": "DingZhen.mp3",
        "reference_name": "template-dingzhen",
        "reference_text": "这是一段用于AI语音训练的示例音频。请以自然、清晰的语速朗读这句话，保持发音准确、语调平稳。朗读时尽量减少停顿与杂音，使整体语音流畅连贯。",
    },
}


SILICONFLOW_VOICES = {
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex": {
        "label": "硅基流动-Alex",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:anna": {
        "label": "硅基流动-Anna",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:anna",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:bella": {
        "label": "硅基流动-Bella",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:bella",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:benjamin": {
        "label": "硅基流动-Benjamin",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:benjamin",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:charles": {
        "label": "硅基流动-Charles",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:charles",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:claire": {
        "label": "硅基流动-Claire",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:claire",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:david": {
        "label": "硅基流动-David",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:david",
    },
    "siliconflow:FunAudioLLM/CosyVoice2-0.5B:diana": {
        "label": "硅基流动-Diana",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:diana",
    },
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO app_kv(key, value) VALUES(?, ?)", (key, value))
    conn.commit()
    return conn


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM app_kv").fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    for key, value in DEFAULT_SETTINGS.items():
        settings.setdefault(key, value)
    return settings


def set_settings(conn: sqlite3.Connection, updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO app_kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    conn.commit()


def list_materials(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, filename, display_name, forced_rank, sort_order, mime_type FROM materials ORDER BY sort_order, id"
    ).fetchall()
    return [dict(row) for row in rows]


def add_materials(conn: sqlite3.Connection, files: list[dict[str, Any]]) -> None:
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM materials").fetchone()[0]
    for index, file in enumerate(files):
        conn.execute(
            """
            INSERT INTO materials(filename, display_name, forced_rank, sort_order, mime_type, content)
            VALUES(?, ?, NULL, ?, ?, ?)
            """,
            (
                file["filename"],
                file["display_name"],
                next_order + index,
                file.get("mime_type"),
                file["content"],
            ),
        )
    conn.commit()


def update_materials(conn: sqlite3.Connection, materials: list[dict[str, Any]]) -> None:
    for order_index, item in enumerate(materials, start=1):
        conn.execute(
            """
            UPDATE materials
            SET display_name = ?, forced_rank = ?, sort_order = ?
            WHERE id = ?
            """,
            (
                item["display_name"],
                item.get("forced_rank") or None,
                order_index,
                item["id"],
            ),
        )
    conn.commit()


def delete_material(conn: sqlite3.Connection, material_id: int) -> None:
    conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
    conn.commit()


def clear_materials(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM materials")
    conn.commit()


def get_material_content(conn: sqlite3.Connection, material_id: int) -> bytes:
    row = conn.execute("SELECT content FROM materials WHERE id = ?", (material_id,)).fetchone()
    if row is None:
        raise KeyError(material_id)
    return row["content"]


def get_material(conn: sqlite3.Connection, material_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, filename, display_name, forced_rank, sort_order, mime_type, content FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    if row is None:
        raise KeyError(material_id)
    return dict(row)


def save_custom_voice(conn: sqlite3.Connection, filename: str, reference_name: str, reference_text: str, mime_type: str | None, content: bytes) -> int:
    cursor = conn.execute(
        """
        INSERT INTO custom_voices(filename, reference_name, reference_text, mime_type, content)
        VALUES(?, ?, ?, ?, ?)
        """,
        (filename, reference_name, reference_text, mime_type, content),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_custom_voices(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, filename, reference_name, reference_text, mime_type FROM custom_voices ORDER BY id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_custom_voice(conn: sqlite3.Connection, voice_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM custom_voices WHERE id = ?", (voice_id,)).fetchone()
    if row is None:
        raise KeyError(voice_id)
    return dict(row)


def save_output(conn: sqlite3.Connection, name: str, mime_type: str, content: bytes) -> None:
    conn.execute(
        """
        INSERT INTO outputs(name, mime_type, content) VALUES(?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET mime_type=excluded.mime_type, content=excluded.content
        """,
        (name, mime_type, content),
    )
    conn.commit()


def get_output(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT name, mime_type, content FROM outputs WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def get_copywriting(conn: sqlite3.Connection) -> dict[str, Any] | None:
    settings = get_settings(conn)
    raw = settings.get("copywriting_json", "")
    if not raw:
        return None
    return json.loads(raw)


def save_copywriting(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    set_settings(conn, {"copywriting_json": json.dumps(payload, ensure_ascii=False)})


def clear_outputs(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM outputs")
    conn.commit()


def delete_output(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM outputs WHERE name = ?", (name,))
    conn.commit()
