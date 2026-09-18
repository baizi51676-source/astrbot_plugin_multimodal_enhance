"""音频解析：元数据、（轻量）频谱文本、（可选）声纹包式深度摘要。

- 轻量频谱：numpy 即可（s16le PCM -> FFT 分段统计）。
- 深度摘要：检测到 librosa 时启用，输出对齐「声纹包」摘要维度
  （BPM/调性/亮度/纹理/质量/温度/情绪/谐波占比/人声带占比等）。
"""

from __future__ import annotations

import importlib.util

BANDS = [
    (20, 60), (60, 120), (120, 250), (250, 500), (500, 1000),
    (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000),
]
BAND_LABELS = ["超低", "低", "低中", "中低", "中", "中高", "高", "很高", "极高"]
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_KEYS = {0, 2, 4, 5, 7, 9, 11}


def has_numpy() -> bool:
    return importlib.util.find_spec("numpy") is not None


def has_librosa() -> bool:
    return importlib.util.find_spec("librosa") is not None


# ---------------- 轻量频谱 ----------------

def spectrum_segments(pcm: bytes, sr: int, seg_sec: float) -> list[dict]:
    if not pcm:
        return []
    try:
        import numpy as np
    except Exception:
        return []
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    seg_len = max(1024, int(sr * max(1.0, seg_sec)))
    out: list[dict] = []
    i = 0
    while i + seg_len // 2 <= len(samples):
        chunk = samples[i:i + seg_len]
        if len(chunk) < max(1024, seg_len // 4):
            break
        window = np.hanning(len(chunk))
        spec = np.abs(np.fft.rfft(chunk * window))
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / sr)
        total = float(spec.sum()) + 1e-9
        bands = []
        for idx, (lo, hi) in enumerate(BANDS):
            m = (freqs >= lo) & (freqs < hi)
            bands.append(round(float(spec[m].sum()) / total * 100.0, 1))
        centroid = float((spec * freqs).sum() / (float(spec.sum()) + 1e-9))
        out.append({
            "t": round(i / sr, 1),
            "centroid_hz": round(centroid),
            "bands": bands,
        })
        i += seg_len
    return out


def spectrum_text(segments: list[dict], max_lines: int = 150) -> str:
    if not segments:
        return ""
    lines = ["图例：" + "、".join(
        f"{label}({lo}-{hi}Hz)" for label, (lo, hi) in zip(BAND_LABELS, BANDS)
    )]
    for seg in segments[:max_lines]:
        bands = " ".join(
            f"{label}{value}%" for label, value in zip(BAND_LABELS, seg["bands"])
        )
        lines.append(f"{seg['t']:.0f}s 质心{seg['centroid_hz']}Hz | {bands}")
    if len(segments) > max_lines:
        lines.append(f"...（共 {len(segments)} 段，已省略其余）")
    # 汇总
    avg_centroid = sum(s["centroid_hz"] for s in segments) // len(segments)
    avg_bands = [sum(s["bands"][i] for s in segments) / len(segments) for i in range(len(BANDS))]
    top_idx = max(range(len(avg_bands)), key=lambda i: avg_bands[i])
    lines.append(
        f"汇总：平均质心{avg_centroid}Hz，能量最强频段为 {BAND_LABELS[top_idx]}"
        f"（{BANDS[top_idx][0]}-{BANDS[top_idx][1]}Hz，均值{avg_bands[top_idx]:.1f}%）"
    )
    return "\n".join(lines)


# ---------------- 声纹包式深度摘要 ----------------

def _describe_brightness(centroid: float) -> str:
    if centroid > 3500:
        return "极亮"
    if centroid > 2500:
        return "亮"
    if centroid > 1800:
        return "中等偏亮"
    if centroid > 1200:
        return "中性"
    if centroid > 800:
        return "暗"
    if centroid > 500:
        return "很暗"
    return "极暗"


def _describe_texture(flatness: float) -> str:
    if flatness > 0.4:
        return "粗粝"
    if flatness > 0.25:
        return "中等"
    if flatness > 0.15:
        return "光滑"
    return "极纯"


def _describe_mass(spread: float) -> str:
    if spread > 2500:
        return "厚实"
    if spread > 1800:
        return "丰满"
    if spread > 1200:
        return "中等"
    if spread > 700:
        return "薄"
    return "极薄"


def _describe_temperature(centroid: float, harmonic_ratio: float, low_mid: float) -> str:
    c_norm = min(1.0, max(0.0, (centroid - 500) / 3500))
    richness = max(0.0, min(1.0, 1.0 - abs(harmonic_ratio - 0.7) / 0.3))
    score = -c_norm * 0.45 + low_mid * 0.2 + richness * 0.35
    if score > 0.5:
        return "温暖"
    if score > 0.25:
        return "偏暖"
    if score > 0.05:
        return "中性"
    if score > -0.2:
        return "偏冷"
    return "冷"


def deep_summary(path: str) -> dict | None:
    """声纹包式摘要（需 librosa）。失败返回 None。"""
    try:
        import librosa
        import numpy as np
    except Exception:
        return None
    try:
        y, sr = librosa.load(path, sr=22050, mono=True)
        if y.size == 0:
            return None
        tempo = librosa.beat.beat_track(y=y, sr=sr)[0]
        bpm = float(np.atleast_1d(tempo)[0])
        rms = librosa.feature.rms(y=y)[0]
        energy_var = float(rms.var())
        cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        brightness = float(cent.mean())
        flatness = float(librosa.feature.spectral_flatness(y=y)[0].mean())
        spread = float(librosa.feature.spectral_bandwidth(y=y, sr=sr)[0].mean())
        texture_zcr = float(librosa.feature.zero_crossing_rate(y=y)[0].mean())
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(chroma.mean(axis=1))) if chroma.size else 0

        S = np.abs(librosa.stft(y))
        H, P = librosa.decompose.hpss(S)
        hsum, psum = float(H.sum()), float(P.sum())
        harmonic_ratio = hsum / (hsum + psum + 1e-8)
        freqs = librosa.fft_frequencies(sr=sr)
        band_vocal = (freqs >= 300) & (freqs <= 3000)
        vocal_ratio = float(H[band_vocal].sum() / (hsum + 1e-8))
        band_low_mid = (freqs >= 150) & (freqs <= 400)
        low_mid = float(H[band_low_mid].sum() / (hsum + 1e-8))

        # 情绪（对齐声纹包 music_emotion 的公式）
        arousal_bpm = min(1.0, max(0.0, (bpm - 60) / 120))
        arousal_bright = min(1.0, max(0.0, (brightness - 1000) / 5000))
        arousal_texture = min(1.0, max(0.0, texture_zcr * 10))
        arousal = round(arousal_bpm * 0.4 + arousal_bright * 0.3
                        + arousal_texture * 0.2 + energy_var * 5 * 0.1, 2)
        valence_major = 0.6 if key_idx in MAJOR_KEYS else 0.3
        valence = round(valence_major * 0.6 + max(0.0, 1 - energy_var * 10) * 0.4, 2)
        if valence >= 0.5 and arousal >= 0.5:
            quadrant = "兴奋/快乐"
        elif valence >= 0.5 and arousal < 0.5:
            quadrant = "平静/满足"
        elif valence < 0.5 and arousal >= 0.5:
            quadrant = "焦虑/愤怒"
        else:
            quadrant = "忧郁/悲伤"

        return {
            "bpm": round(bpm),
            "key": KEYS[key_idx % 12],
            "brightness_hz": round(brightness),
            "spread_hz": round(spread),
            "flatness": round(flatness, 3),
            "zcr": round(texture_zcr, 4),
            "harmonic_ratio": round(harmonic_ratio, 2),
            "vocal_ratio": round(vocal_ratio, 2),
            "energy_var": round(energy_var, 4),
            "valence": valence,
            "arousal": arousal,
            "quadrant": quadrant,
            "timbre": {
                "brightness": _describe_brightness(brightness),
                "texture": _describe_texture(flatness),
                "mass": _describe_mass(spread),
                "temperature": _describe_temperature(brightness, harmonic_ratio, low_mid),
            },
        }
    except Exception:
        return None


def deep_summary_text(summary: dict) -> str:
    timbre = summary.get("timbre") or {}
    lines = [
        f"调性 {summary.get('key', '?')} / BPM {summary.get('bpm', '?')}",
        "音色：亮度{亮度}、纹理{纹理}、质量{质量}、温度{温度}".format(
           亮度=timbre.get("brightness", "?"), 纹理=timbre.get("texture", "?"),
            质量=timbre.get("mass", "?"), 温度=timbre.get("temperature", "?")),
        f"谱质心 {summary.get('brightness_hz', '?')}Hz / 展宽 {summary.get('spread_hz', '?')}Hz"
        f" / 平坦度 {summary.get('flatness', '?')}",
        f"谐波占比 {summary.get('harmonic_ratio', '?')} / 人声带占比 {summary.get('vocal_ratio', '?')}",
        "情绪：Valence {v} / Arousal {a}（{q}）".format(
            v=summary.get("valence", "?"), a=summary.get("arousal", "?"),
            q=summary.get("quadrant", "?")),
    ]
    return "\n".join(lines)