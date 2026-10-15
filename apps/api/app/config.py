from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode
from typing import Annotated
import json

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8', extra='ignore')
    app_env: str = 'development'
    database_url: str = Field(default='sqlite:///./runtime/local.db', repr=False)
    migration_database_url: str = Field(default='', repr=False)
    worker_database_url: str = Field(default='', repr=False)
    dashscope_api_key: str = Field(default='', repr=False)
    dashscope_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    dashscope_model: str = 'qwen-plus'
    dashscope_chat_model: str = ''
    native_text_backend: str = 'dashscope'
    native_text_model_path: Path | None = None
    native_text_max_concurrency: int = Field(default=1, ge=1, le=1)
    native_text_max_context_tokens: int = Field(default=16384, ge=1024, le=32768)
    native_text_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    # Daily application quotas are optional: 0 means unlimited, positive values
    # retain explicit deployment limits. Usage is recorded in either mode.
    native_local_text_daily_token_budget: int = Field(default=0, ge=0)
    native_resident_text_base_url: str = 'http://127.0.0.1:18891/v1'
    native_resident_text_model: str = 'housignal-qwen3-vl-32b-text'
    native_resident_text_token_path: Path | None = None
    native_resident_text_timeout_seconds: int = Field(default=285, ge=30, le=285)
    daily_token_budget: int = Field(default=0, ge=0)
    development_token_budget: int = Field(default=0, ge=0)
    dashscope_vision_model: str = 'qwen-vl-plus'
    dashscope_asr_model: str = 'qwen3-asr-flash'
    dashscope_image_model: str = ''
    dashscope_video_model: str = ''
    native_video_backend: str = 'dashscope'
    native_video_base_url: str = ''
    native_video_model: str = 'minimax-h3-dense8'
    native_video_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    dashscope_music_model: str = ''
    dashscope_tts_model: str = ''
    dashscope_tts_voice: str = ''
    native_whisper_model_path: Path | None = None
    native_background_model_path: Path | None = None
    native_music_base_url: str = ''
    native_music_provider: str = 'dashscope'
    native_music_api_key: str = Field(default='', repr=False)
    native_music_download_hosts: Annotated[list[str], NoDecode] = []
    native_short_link_base_url: str = ''
    native_short_link_api_key: str = Field(default='', repr=False)
    native_short_link_allowed_hosts: Annotated[list[str], NoDecode] = []
    native_notification_webhook_url: str = Field(default='', repr=False)
    native_notification_key: str = Field(default='', repr=False)
    native_notification_allowed_hosts: Annotated[list[str], NoDecode] = []
    secret_key: str = Field(default='local-development-only-change-before-deploy', repr=False)
    pii_encryption_key: str = Field(default='', repr=False)
    storage_root: Path = ROOT / 'runtime' / 'storage'
    bootstrap_email: str = 'admin@housignal.local'
    bootstrap_password: str = Field(default='', repr=False)
    allowed_origins: Annotated[list[str], NoDecode] = ['http://localhost:5177', 'http://127.0.0.1:5177', 'http://localhost:8017', 'http://127.0.0.1:8017']
    ffmpeg_binary: str = 'ffmpeg'
    ffprobe_binary: str = 'ffprobe'

    @field_validator('allowed_origins', 'native_music_download_hosts', 'native_short_link_allowed_hosts',
                     'native_notification_allowed_hosts', mode='before')
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return json.loads(value) if value.lstrip().startswith('[') else [part.strip() for part in value.split(',') if part.strip()]
        return value

    @field_validator('native_whisper_model_path', 'native_background_model_path', 'native_text_model_path', 'native_resident_text_token_path', mode='before')
    @classmethod
    def empty_model_path(cls, value):
        return None if value == '' else value


settings = Settings()
if settings.app_env == 'production' and (not settings.pii_encryption_key or settings.secret_key == 'local-development-only-change-before-deploy'):
    raise RuntimeError('生产环境需要独立安全密钥')
