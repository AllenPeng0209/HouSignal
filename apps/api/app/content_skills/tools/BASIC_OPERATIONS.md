# Basic operation migration

The reference was the local Easel shared image_ops.py, audio_ops.py and
video_ops.py CLI at upstream revision 40f08d96f7eaaad19500cb121c4ca23b542fd89e.
The functions below execute within this project; these names are API parameters,
not invocations of the original CLI or its service.

| Source module | Source operation | Native skill and operation | Runtime dependency |
| --- | --- | --- | --- |
| image_ops | resize | image-editing / resize | Pillow |
| image_ops | crop | image-editing / crop | Pillow |
| image_ops | pad | image-editing / pad | Pillow |
| image_ops | convert | image-editing / convert | Pillow |
| image_ops | compress | image-editing / compress | Pillow |
| image_ops | watermark | image-editing / watermark | Pillow, system font for text |
| image_ops | round | image-editing / round | Pillow |
| image_ops | collage | image-editing / collage | Pillow |
| image_ops | thumbnail | image-editing / thumbnail | Pillow |
| image_ops | info | image-editing / info | Pillow |
| audio_ops | info | audio-editing / info | FFprobe |
| audio_ops | trim | audio-editing / trim | FFmpeg |
| audio_ops | convert | audio-editing / convert | FFmpeg |
| audio_ops | normalize | audio-editing / normalize | FFmpeg loudnorm |
| audio_ops | extract | audio-editing / extract, extract-audio | FFmpeg |
| audio_ops | concat | audio-editing / concat | FFmpeg |
| audio_ops | fade | audio-editing / fade | FFmpeg |
| audio_ops | speed | audio-editing / speed | FFmpeg atempo |
| audio_ops | denoise | audio-editing or audio-denoise / denoise | Tier 1/2 local filters; Tier 3 also needs supplied RNNoise weights and arnndn |
| video_ops | cut | video-editing / cut, trim | FFmpeg |
| video_ops | concat | video-editing / concat | FFmpeg |
| video_ops | speed | video-editing / speed | FFmpeg |
| video_ops | silence-cut, mute-cut | video-editing / silence-cut, mute-cut, remove-silence, jumpcut | Real silencedetect plus synchronized trim/atrim |
| video_ops | text | video-editing / text | Pillow glyphs, FFmpeg |
| video_ops | aspect | video-editing / aspect, reframe | FFmpeg; smart face focus additionally needs OpenCV |
| video_ops | frame | video-editing / frame, thumbnail | FFmpeg |
| video_ops | gif | video-editing / gif | Real palettegen/paletteuse |
| video_ops | compress | video-editing / compress | FFmpeg |
| video_ops | bgm | video-editing / bgm | FFmpeg, authorized selected music asset |
| video_ops | watermark | video-editing / watermark | FFmpeg, authorized selected image asset |
| video_ops | info | video-editing / info | FFprobe |

Shared image enhancement, background replacement, cards, documents, 24 chart
types, GIF infographics, subtitles, music and video assembly have separate native
handlers. Every one of the 44 registered media skills has an execution handler.
The compatibility claim is functional: independent Pillow/SVG/HTML layout and
native FFmpeg production do not promise identical pixels or the original
Remotion editor interface. PDF parsing extracts real text and embedded images;
formula/vector reconstruction still depends on what the PDF contains.

Actual model-based paths use only project configuration. Configured providers
are checked at execution; there has been no real paid-provider acceptance here.
Ordinary Edge TTS, Model Studio HTTP Qwen/Wan media and explicitly configured
DashScope/Suno-compatible music are implemented. Voice cloning is project-owned
enrollment plus synthesis; private voice IDs must be authorized for that project.
Timestamp-free ASR uses the configured project ASR model. Timed subtitles require
supplied actual segments or a project timestamp-capable ASR model.

Background removal runs a configured project-local ONNX model. Optional precise
alpha matting executes PyMatting when installed. No model is downloaded or loaded
from a legacy cache. Missing tools, weights, configured models, appropriate codec
filters or voice registration are reported as setup, never replaced by a fake
media file.
