Original code portions: ZJU-REAL/Easel, Apache License 2.0.
Original source snapshot revision: 40f08d96f7eaaad19500cb121c4ca23b542fd89e.
The complete upstream Apache license is retained in this directory's LICENSE.

Louxiang migration modifications: replaced CLI entry points, sys.exit, external
environment-file and profile discovery, cross-script invocations and service
calls with Python function dispatch. Project assets and output scope come from
the authorized project worker. The native modules add parameter validation,
bounded processing, real output verification and project usage callbacks.
No runtime import or file read depends on the Easel checkout or its archive.

The media recipes originate in image_ops.py, img_enhance.py, meme_ops.py,
audio_ops.py, audio_mix.py, audio_viz.py, video_ops.py, reframe.py, slideshow.py,
intro_outro.py, beatsync.py, chromakey.py, highlight_cut.py, subtitle_ops.py,
asr.py, tts.py, multivoice.py, ai_image.py and ai_video.py. Document/card/chart
layout algorithms originate in render_card.py, mindmap.py, doc_convert.py,
card_audit.py, gif_chart.py and data-report/report.py. The gzh Markdown parser
and themes are independently implemented; no AGPL gzh-design source is copied.

Media adapters were checked against these official API contracts on 2026-10-13:
- https://www.alibabacloud.com/help/en/model-studio/qwen-image-generation-and-editing-api-reference
- https://www.alibabacloud.com/help/en/model-studio/qwen-image-api
- https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference
- https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api
- https://www.alibabacloud.com/help/en/model-studio/non-realtime-speech-recognition-user-guide

Model Studio's compatible Qwen3-ASR-Flash chat response does not contain timing
fields. Precise subtitles therefore require actual supplied cues or a configured
project-local timestamp-capable ASR adapter/model. Timing is never estimated.
Music generation remains setup until a supported project adapter is provided.
