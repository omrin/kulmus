"""Speaker diarization: the pyannote 3.1 pipeline (the one transcribe.ivrit.ai uses) on ONNX models, no torch.

1. segmentation-3.0 (powerset) on 10 s windows every 1 s -> up to 3 "local" speakers per window
2. a WeSpeaker ResNet34 voiceprint per (window, local speaker), computed like pyannote: fbank of the whole
   window minus its mean, the network once per window, then the speaker's frames pooled (weighted mean/std)
   - using only frames where nobody else talks, unless there are too few
3. agglomerative clustering of the voiceprints (centroid linkage, euclidean on unit vectors); clusters with
   fewer than MIN_CLUSTER_SIZE voiceprints join the closest large one
4. every local speaker goes to the closest cluster centroid (cosine)
5. per frame, the `count` most active clusters (count = estimated number of simultaneous speakers)

The ONNX exports are sherpa-onnx's; the WeSpeaker export applies no mean normalisation and pools inside the
graph, so the pre-pooling tensor is exposed (protobuf edit at load time) and pooling is done here.
Output: [(start_s, end_s, speaker_int)] sorted by start."""

from __future__ import annotations

import math

import numpy as np

SR = 16_000
# pyannote 3.1 uses 0.7045 (tuned on English benchmarks); on 9 Hebrew recordings scored against transcribe.ivrit.ai,
# 0.75 agreed best (0.761 vs 0.734), keeping speakers apart in meetings without splitting two-person talks
THRESHOLD = 0.75
MIN_CLUSTER_SIZE = 12            # voiceprints (~1 s steps): smaller clusters join the closest large one
PRECLUSTER_ABOVE = 1500          # voiceprints; above this, near-identical ones are grouped first (memory)


class Cancelled(Exception):
    pass


class Segmenter:
    def __init__(self, model_path, threads: int = 4):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.window = int(meta["window_size"])                 # 160000 samples = 10 s
        self.step = self.window // 10                          # 1 s
        self.frame_shift = int(meta["receptive_field_shift"])  # 270 samples
        self.receptive = int(meta["receptive_field_size"])     # 991 samples
        self.num_speakers = int(meta["num_speakers"])          # 3 local speakers
        self.mapping = _powerset_mapping(int(meta["num_classes"]), self.num_speakers,
                                         int(meta["powerset_max_classes"]))
        self.in_name = self.sess.get_inputs()[0].name

    def chunks(self, audio):
        """Start samples of the 10 s windows (the last one is zero-padded)."""
        n = len(audio)
        if n <= self.window:
            return [0]
        starts = list(range(0, n - self.window + 1, self.step))
        if (n - self.window) % self.step:
            starts.append(starts[-1] + self.step)
        return starts

    def __call__(self, audio, progress=None, cancelled=lambda: False, batch=32):
        """-> binary (num_chunks, num_frames, local_speakers) array and the chunk start samples."""
        starts = self.chunks(audio)
        out = []
        for i in range(0, len(starts), batch):
            if cancelled():
                raise Cancelled()
            x = np.zeros((len(starts[i:i + batch]), 1, self.window), np.float32)
            for j, s in enumerate(starts[i:i + batch]):
                piece = audio[s:s + self.window]
                x[j, 0, :len(piece)] = piece
            y = self.sess.run(None, {self.in_name: x})[0]
            out.append(self.mapping[np.argmax(y, axis=-1)])
            if progress:
                progress(min(1.0, (i + batch) / len(starts)))
        return np.concatenate(out).astype(np.float32), starts


def _powerset_mapping(num_classes, num_speakers, max_classes):
    mapping = np.zeros((num_classes, num_speakers), np.float32)
    k = 1
    for size in range(1, max_classes + 1):
        if size == 1:
            for j in range(num_speakers):
                mapping[k, j] = 1
                k += 1
        else:
            for j in range(num_speakers):
                for m in range(j + 1, num_speakers):
                    mapping[k, j] = mapping[k, m] = 1
                    k += 1
    return mapping


# ------------------------------------------------------------------ voiceprints the way pyannote computes them

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(num, payload: bytes) -> bytes:
    return _varint(num << 3 | 2) + _varint(len(payload)) + payload


def _read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, i


def _fields(buf):
    """(field number, wire type, value) of a protobuf message; length-delimited values as memoryviews."""
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _read_varint(buf, i)
        elif wt == 2:
            n, i = _read_varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        else:
            raise ValueError(f"protobuf wire type {wt}")
        yield num, wt, v


def onnx_initializers(model: bytes, names) -> dict:
    """Float tensors stored in an ONNX model (raw_data), without the onnx package."""
    out = {}
    buf = memoryview(model)
    for num, _, graph in _fields(buf):
        if num != 7:                              # ModelProto.graph
            continue
        for gnum, _, t in _fields(graph):
            if gnum != 5:                         # GraphProto.initializer (TensorProto)
                continue
            dims, name, raw = [], None, None
            for tnum, wt, v in _fields(t):
                if tnum == 1:
                    if wt == 0:
                        dims.append(v)
                    else:                         # packed
                        j = 0
                        while j < len(v):
                            d, j = _read_varint(v, j)
                            dims.append(d)
                elif tnum == 8:
                    name = bytes(v).decode()
                elif tnum == 9:
                    raw = bytes(v)
            if name in names and raw is not None:
                out[name] = np.frombuffer(raw, np.float32).reshape(dims).copy()
    return out


def with_output(model: bytes, name: str) -> bytes:
    """Makes an intermediate tensor a graph output (the appended graph message is merged into the graph)."""
    return model + _field(7, _field(12, _field(1, name.encode())))


def fbank(wav):
    """torchaudio.compliance.kaldi.fbank as pyannote's WeSpeaker uses it: 80 mel bins, 25/10 ms, hamming,
    dither 0, on samples scaled to int16. -> (frames, 80)"""
    x = wav.astype(np.float64) * 32768.0
    flen, fshift, nfft = 400, 160, 512
    n = 1 + (len(x) - flen) // fshift
    fr = x[np.arange(flen)[None, :] + fshift * np.arange(n)[:, None]]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = fr - 0.97 * np.concatenate([fr[:, :1], fr[:, :-1]], axis=1)          # preemphasis (replicate pad)
    win = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))
    power = np.abs(np.fft.rfft(fr * win, nfft)) ** 2
    return np.log(np.maximum(power @ _mel_banks().T, np.finfo(np.float32).eps)).astype(np.float32)


_BANKS = None


def _mel_banks(num_mel=80, nfft=512):
    global _BANKS
    if _BANKS is None:
        def mel(f):
            return 1127.0 * np.log(1.0 + f / 700.0)
        centers = np.linspace(mel(20.0), mel(SR / 2), num_mel + 2)
        fft_mel = mel(np.arange(nfft // 2) * SR / nfft)
        banks = np.zeros((num_mel, nfft // 2 + 1))
        for m in range(num_mel):
            lo, c, hi = centers[m:m + 3]
            banks[m, :nfft // 2] = np.maximum(0, np.minimum((fft_mel - lo) / (c - lo), (hi - fft_mel) / (hi - c)))
        _BANKS = banks
    return _BANKS


def _interp(w, size):
    """torch.nn.functional.interpolate(mode='linear', align_corners=False) of 1-D weights."""
    n = len(w)
    src = np.clip((np.arange(size) + 0.5) * n / size - 0.5, 0, n - 1)
    lo = np.floor(src).astype(int)
    hi = np.minimum(lo + 1, n - 1)
    return w[lo] * (hi - src if n > 1 else 1) + w[hi] * (src - lo)


class Voiceprints:
    """WeSpeaker ResNet34 voiceprints exactly as pyannote computes them: fbank of the whole 10 s window with its
    mean removed, the network once per window, and per speaker a weighted mean/std pooling of the last layer."""

    POOL_INPUT = "343"            # last ReLU before statistics pooling in wespeaker_en_voxceleb_resnet34_LM.onnx

    def __init__(self, model_path, threads: int = 4):
        import onnxruntime as ort
        raw = open(model_path, "rb").read()
        w = onnx_initializers(raw, {"model.seg_1.weight", "model.seg_1.bias"})
        self.W, self.b = w["model.seg_1.weight"].astype(np.float64), w["model.seg_1.bias"].astype(np.float64)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(with_output(raw, self.POOL_INPUT), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self.in_name = self.sess.get_inputs()[0].name

    def frames(self, wav):
        """-> (channels*freq, T) frame-level features of one waveform."""
        f = fbank(wav)
        f -= f.mean(axis=0)
        h = self.sess.run([self.POOL_INPUT], {self.in_name: f[None]})[0][0]
        return h.reshape(-1, h.shape[-1]).astype(np.float64)

    def pool(self, seq, weights=None):
        """pyannote StatsPool + the embedding layer. seq: (D, T); weights: any length (interpolated to T)."""
        if weights is None:
            mean = seq.mean(1)
            std = seq.std(1, ddof=1)
        else:
            w = _interp(np.asarray(weights, np.float64), seq.shape[1])
            v1 = w.sum() + 1e-8
            mean = (seq * w).sum(1) / v1
            v2 = (w ** 2).sum()
            std = np.sqrt(((seq - mean[:, None]) ** 2 * w).sum(1) / (v1 - v2 / v1 + 1e-8))
        return self.W @ np.concatenate([mean, std]) + self.b

    def embed(self, wav):
        """Voiceprint of a whole waveform (e.g. a known speaker's speech)."""
        return self.pool(self.frames(wav))


def embeddings(audio, binary, starts, seg: Segmenter, vp: Voiceprints, progress=None, cancelled=lambda: False):
    """Voiceprint per (chunk, local speaker) like pyannote's get_embeddings(exclude_overlap=True)."""
    num_chunks, num_frames, num_local = binary.shape
    clean = binary * (binary.sum(axis=2, keepdims=True) < 2)
    min_frames = math.ceil(num_frames * 640 / seg.window)         # the network needs ~40 ms
    nframes = np.zeros((num_chunks, num_local), int)
    out = None
    for c in range(num_chunks):
        if cancelled():
            raise Cancelled()
        speakers = [s for s in range(num_local) if binary[c, :, s].any()]
        if not speakers:
            continue
        wav = np.zeros(seg.window, np.float32)
        piece = audio[starts[c]:starts[c] + seg.window]
        wav[:len(piece)] = piece
        seq = vp.frames(wav)
        for s in speakers:
            mask = clean[c, :, s] if clean[c, :, s].sum() > min_frames else binary[c, :, s]
            e = vp.pool(seq, mask)
            if out is None:
                out = np.full((num_chunks, num_local, len(e)), np.nan)
            out[c, s] = e
            nframes[c, s] = int(mask.sum())
        if progress and c % 20 == 0:
            progress(c / num_chunks)
    if out is None:
        out = np.full((num_chunks, num_local, 1), np.nan)
    return out, nframes


def _centroid_linkage(x, weights, threshold, until=None):
    """Agglomerative clustering with centroid linkage (euclidean). x: (n, d) points, weights: their sizes.
    Merges the closest pair of centroids while their distance <= threshold, or, with `until`, until that many
    clusters are left. Returns cluster labels 0..k-1 for the n points."""
    n = len(x)
    cent = x.astype(np.float64).copy()
    size = weights.astype(np.float64).copy()
    alive = np.ones(n, bool)
    parent = np.arange(n)
    sq = (cent ** 2).sum(1)
    d = np.sqrt(np.maximum(sq[:, None] + sq[None, :] - 2 * cent @ cent.T, 0))
    np.fill_diagonal(d, np.inf)
    nn = d.argmin(1)
    nd = d[np.arange(n), nn]
    left = n
    while left > 1:
        live = np.flatnonzero(alive)
        i = live[np.argmin(nd[live])]
        j = nn[i]
        dist = nd[i]
        if until is None and dist > threshold:
            break
        if until is not None and left <= until:
            break
        # merge j into i
        cent[i] = (size[i] * cent[i] + size[j] * cent[j]) / (size[i] + size[j])
        size[i] += size[j]
        alive[j] = False
        parent[j] = i
        d[j, :] = d[:, j] = np.inf
        left -= 1
        row = np.sqrt(np.maximum(((cent - cent[i]) ** 2).sum(1), 0))
        row[~alive] = np.inf
        row[i] = np.inf
        d[i, :] = row
        d[:, i] = row
        nn[i] = row.argmin()
        nd[i] = row[nn[i]]
        # rows whose nearest neighbour was i or j, or that are now closer to i
        stale = alive & ((nn == i) | (nn == j))
        stale[i] = False
        for r in np.flatnonzero(stale):
            nn[r] = d[r].argmin()
            nd[r] = d[r, nn[r]]
        closer = alive & (row < nd)
        nn[closer] = i
        nd[closer] = row[closer]
    root = np.arange(n)
    for k in range(n):
        r = k
        while parent[r] != r:
            r = parent[r]
        root[k] = r
    _, labels = np.unique(root, return_inverse=True)
    return labels


def _precluster(x, sim=0.97, recent=8):
    """Consecutive windows of one speaker give almost identical voiceprints: groups them (greedy, against the
    most recent groups) so that the clustering's distance matrix stays small in long recordings."""
    groups, sums, labels = [], [], np.empty(len(x), int)
    for i, v in enumerate(x):
        best, best_sim = -1, sim
        for g in groups[-recent:]:
            c = sums[g] / np.linalg.norm(sums[g])
            s_ = float(c @ v)
            if s_ >= best_sim:
                best, best_sim = g, s_
        if best < 0:
            best = len(sums)
            groups.append(best)
            sums.append(v.copy())
        else:
            sums[best] = sums[best] + v
            groups.remove(best)
            groups.append(best)
        labels[i] = best
    return labels


def cluster(train, num_clusters=None, threshold=THRESHOLD, min_cluster=MIN_CLUSTER_SIZE):
    """pyannote's AgglomerativeClustering.cluster on voiceprints (n, d). -> labels 0..k-1."""
    n = len(train)
    if n == 1:
        return np.zeros(1, int)
    x = train / np.linalg.norm(train, axis=1, keepdims=True)
    min_size = min(min_cluster, max(1, round(0.1 * n)))
    if n > PRECLUSTER_ABOVE:
        micro = _precluster(x)
    else:
        micro = np.arange(n)
    groups = np.unique(micro)
    gx = np.vstack([x[micro == g].mean(0) for g in groups])
    gw = np.array([np.sum(micro == g) for g in groups], float)
    if num_clusters:
        glabels = _centroid_linkage(gx, gw, threshold, until=num_clusters)
    else:
        glabels = _centroid_linkage(gx, gw, threshold)
    labels = glabels[np.searchsorted(groups, micro)]
    uniq, counts = np.unique(labels, return_counts=True)
    large = uniq[counts >= min_size]
    if num_clusters and len(large) < num_clusters:
        large = uniq[np.argsort(-counts)[:num_clusters]]     # asked for N speakers: keep the N largest
    if len(large) == 0:
        return np.zeros(n, int)
    small = [k for k in uniq if k not in set(large)]
    if small:
        lc = np.vstack([x[labels == k].mean(0) for k in large])
        lc /= np.linalg.norm(lc, axis=1, keepdims=True)
        for k in small:
            sc = x[labels == k].mean(0)
            sc /= np.linalg.norm(sc)
            labels[labels == k] = large[int(np.argmax(lc @ sc))]
    _, labels = np.unique(labels, return_inverse=True)
    return labels


def diarize(audio, seg: Segmenter, vp: Voiceprints, num_speakers=0, progress=None, cancelled=lambda: False):
    """-> [(start_s, end_s, speaker_int)] sorted by start. num_speakers 0 = estimate."""
    def stage(lo, hi):
        return (lambda p: progress(lo + (hi - lo) * p)) if progress else None

    binary, starts = seg(audio, stage(0.0, 0.15), cancelled)
    emb, _ = embeddings(audio, binary, starts, seg, vp, stage(0.15, 0.95), cancelled)
    return assign(binary, emb, seg, len(audio), num_speakers)


def assign(binary, emb, seg, num_samples, num_speakers=0, threshold=THRESHOLD, min_cluster=MIN_CLUSTER_SIZE):
    """Clustering and reconstruction (steps 3-5) from the segmentation and voiceprints."""
    num_chunks, num_frames, num_local = binary.shape
    active = binary.sum(axis=1) > 0                                   # (chunks, local)
    valid = active & ~np.isnan(emb).any(axis=2)
    ci, si = np.nonzero(valid)
    if len(ci) == 0:
        return []
    train = emb[ci, si]
    labels = cluster(train, num_speakers or None, threshold, min_cluster)
    k = labels.max() + 1
    centroids = np.vstack([train[labels == j].mean(0) for j in range(k)])
    cn = centroids / np.linalg.norm(centroids, axis=1, keepdims=True)
    hard = np.full((num_chunks, num_local), -2)
    flat = emb.reshape(-1, emb.shape[2])
    ok = ~np.isnan(flat).any(axis=1)
    sims = np.full((len(flat), k), -np.inf)
    sims[ok] = (flat[ok] / np.linalg.norm(flat[ok], axis=1, keepdims=True)) @ cn.T
    hard_flat = np.where(ok, sims.argmax(1), -2)
    hard[:] = hard_flat.reshape(num_chunks, num_local)
    hard[~active] = -2

    # aggregate over the overlapping windows on the global frame grid
    per_chunk = seg.step / seg.frame_shift                       # global frames per window step
    total = int(math.ceil(((num_chunks - 1) * seg.step + seg.window) / seg.frame_shift)) + 1
    act = np.zeros((total, k))
    cnt_sum = np.zeros(total)
    cnt_n = np.zeros(total)
    for c in range(num_chunks):
        o = int(c * per_chunk + 0.5)
        sl = slice(o, o + num_frames)
        cnt_sum[sl] += binary[c].sum(axis=1)
        cnt_n[sl] += 1
        for j in np.unique(hard[c]):
            if j < 0:
                continue
            act[sl, j] += binary[c][:, hard[c] == j].max(axis=1)
    count = np.rint(cnt_sum / np.maximum(cnt_n, 1)).astype(int)
    stop = int(num_samples / seg.frame_shift)
    act, count = act[:stop], count[:stop]
    order = np.argsort(-act, axis=1)
    on = np.zeros_like(act, bool)
    for f in np.flatnonzero(count > 0):
        on[f, order[f, :min(count[f], k)]] = True

    scale = seg.frame_shift / SR
    offset = seg.receptive / SR / 2
    turns = []
    for j in range(k):
        f = np.flatnonzero(on[:, j])
        if not len(f):
            continue
        edges = np.flatnonzero(np.diff(f) > 1)
        for a, b in zip(np.r_[f[0], f[edges + 1]], np.r_[f[edges], f[-1]]):
            turns.append((a * scale + offset, (b + 1) * scale + offset, j))
    turns.sort()
    return turns
