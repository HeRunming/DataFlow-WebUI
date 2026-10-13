// Mirrors backend/app/services/media_assets.py. Keep the extension lists and
// parameter-name patterns in sync with that module.
import axios from '@/axios/config.js'

export const TEXT = 'text'
export const IMAGE = 'image'
export const AUDIO = 'audio'
export const VIDEO = 'video'

const EXT_TO_MODALITY = {}
for (const ext of ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tif', 'tiff', 'svg']) EXT_TO_MODALITY[ext] = IMAGE
for (const ext of ['wav', 'mp3', 'flac', 'aac', 'ogg', 'm4a', 'opus', 'wma']) EXT_TO_MODALITY[ext] = AUDIO
for (const ext of ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv', 'm4v']) EXT_TO_MODALITY[ext] = VIDEO

const PARAM_PATTERNS = [
    [IMAGE, /(^|_)(image|images|img|imgs|picture|photo)(_|$)/],
    [AUDIO, /(^|_)(audio|audios|speech|wav|voice)(_|$)/],
    [VIDEO, /(^|_)(video|videos|clip|clips)(_|$)/]
]

const DATA_URI = /^data:(image|audio|video)\/[\w.+-]+;base64,/i

function extension(value) {
    const base = value.split('?')[0].split('#')[0]
    const name = base.split('/').pop()
    const dot = name.lastIndexOf('.')
    return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

export function sniffValueModality(value) {
    if (Array.isArray(value)) {
        const kinds = new Set(value.map(sniffValueModality))
        return kinds.size === 1 ? [...kinds][0] : TEXT
    }
    if (typeof value !== 'string') return TEXT
    const v = value.trim()
    if (!v || v.includes('\n') || v.length > 4096) return TEXT
    const m = v.match(DATA_URI)
    if (m) return m[1].toLowerCase()
    return EXT_TO_MODALITY[extension(v)] || TEXT
}

export function inferParamModality(paramName) {
    const name = String(paramName || '').toLowerCase()
    for (const [modality, re] of PARAM_PATTERNS) if (re.test(name)) return modality
    return TEXT
}

// Column-level majority vote over sampled records (same rule as the backend's
// infer_field_modalities, minus the column-name fallback for extension-less URLs).
export function infer_field_modalities(records) {
    const votes = {}
    const counts = {}
    for (const record of records || []) {
        if (!record || typeof record !== 'object') continue
        for (const [key, value] of Object.entries(record)) {
            if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) continue
            counts[key] = (counts[key] || 0) + 1
            const modality = sniffValueModality(value)
            votes[key] = votes[key] || {}
            votes[key][modality] = (votes[key][modality] || 0) + 1
        }
    }
    const result = {}
    for (const key of Object.keys(counts)) {
        const [best, n] = Object.entries(votes[key]).sort((a, b) => b[1] - a[1])[0]
        result[key] = best !== TEXT && n * 2 > counts[key] ? best : TEXT
    }
    return result
}

// Histogram bucket for a media cell: its file extension (".png", "url", ...).
export function mediaBucket(value) {
    const first = Array.isArray(value) ? value[0] : value
    if (typeof first !== 'string') return '(other)'
    if (DATA_URI.test(first)) return 'data-uri'
    const ext = extension(first)
    const prefix = /^https?:\/\//i.test(first) ? 'url ' : ''
    return ext ? `${prefix}.${ext}` : prefix.trim() || '(no ext)'
}

export function isMediaModality(modality) {
    return modality === IMAGE || modality === AUDIO || modality === VIDEO
}

export function isRemoteOrInline(value) {
    return typeof value === 'string' && (/^https?:\/\//i.test(value) || DATA_URI.test(value))
}

function buildUrl(path, params) {
    const query = Object.entries(params)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&')
    const base = (axios.defaults.baseURL || '').replace(/\/+$/, '')
    return `${base}${path}?${query}`
}

// source: { type: 'dataset', datasetId } | { type: 'task', taskId, step, node }
export function mediaUrl(source, row, field, index = 0) {
    if (!source) return null
    if (source.type === 'dataset' && source.datasetId) {
        return buildUrl(`/api/v1/datasets/${encodeURIComponent(source.datasetId)}/media`, { field, row, index })
    }
    if (source.type === 'task' && source.taskId) {
        return buildUrl(`/api/v1/tasks/execution/${encodeURIComponent(source.taskId)}/media`, {
            field,
            row,
            index,
            step: source.node ? null : source.step,
            node: source.node
        })
    }
    return null
}
