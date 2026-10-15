"""Shared public limits for short clips and narrated long-form production."""
SHORT_MIN_SECONDS = 15
SHORT_MAX_SECONDS = 60
LONG_MIN_SECONDS = 300
MAX_SECONDS = 600
MAX_SHOTS = 120


def supported_duration(seconds):
    return type(seconds) is int and (SHORT_MIN_SECONDS <= seconds <= SHORT_MAX_SECONDS
                                    or LONG_MIN_SECONDS <= seconds <= MAX_SECONDS)


def validate_duration(seconds):
    if not supported_duration(seconds):
        raise ValueError('短视频支持15至60秒；长视频支持5至10分钟，请选择对应档位')
    return seconds


def chapter_count(seconds):
    validate_duration(seconds)
    return max(6, min(10, round(seconds / 60))) if seconds > SHORT_MAX_SECONDS else 1
