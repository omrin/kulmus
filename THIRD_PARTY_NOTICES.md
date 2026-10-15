# Third-party components

| Component | Use | License |
|---|---|---|
| [ivrit.ai whisper-large-v3-turbo (CTranslate2)](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2) | Hebrew transcription model (downloaded on first run) | Apache-2.0 |
| [OpenAI Whisper](https://github.com/openai/whisper) | base model architecture | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) | inference | MIT |
| Silero VAD (bundled with faster-whisper) | voice activity detection | MIT |
| [pyannote.audio 3.1](https://github.com/pyannote/pyannote-audio) speaker-diarization pipeline (algorithm re-implemented in `diarize.py`) | speaker diarization | MIT |
| [pyannote segmentation 3.0](https://huggingface.co/pyannote/segmentation-3.0) (ONNX export by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), Apache-2.0) | speaker turn segmentation | MIT |
| [WeSpeaker ResNet34 (VoxCeleb)](https://github.com/wenet-e2e/wespeaker) | speaker embeddings | code Apache-2.0; pretrained weights [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (they follow the VoxCeleb dataset license) — © WeSpeaker authors |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | model runtime | MIT |
| [Qt / PySide6](https://www.qt.io/qt-for-python) | user interface, audio playback | LGPL-3.0 (dynamically linked, unmodified) |
| [FFmpeg](https://ffmpeg.org) via [PyAV](https://github.com/PyAV-Org/PyAV) and Qt Multimedia | audio decoding/encoding | LGPL-2.1+ |
| [PortAudio](http://www.portaudio.com) via [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) | microphone recording | MIT |
| [SoundCard](https://github.com/bastibe/SoundCard) | system-audio (loopback) recording | BSD-3-Clause |
| [NumPy](https://numpy.org) | audio processing | BSD-3-Clause |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub) | model download | Apache-2.0 |
| [Feather Icons](https://feathericons.com) (shapes) | icons | MIT |

The LGPL libraries are used as unmodified shared libraries; you may replace them in an installed copy
(e.g. the Qt or FFmpeg libraries inside the application folder) with compatible versions.
