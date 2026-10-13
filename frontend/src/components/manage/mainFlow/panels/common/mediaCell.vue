<template>
    <div class="media-cell" :class="[{ dark: theme === 'dark' }]">
        <template v-if="resolvedModality === 'text' || failed || !src">
            <p :title="text">{{ text }}</p>
            <span v-if="failed" class="media-error">{{ local('Media failed to load') }}</span>
        </template>
        <template v-else>
            <img v-if="resolvedModality === 'image'" :src="src" :alt="text" :title="text" loading="lazy" class="media-thumb"
                @click="expanded = !expanded" :class="[{ expanded }]" @error="failed = true" />
            <audio v-else-if="resolvedModality === 'audio'" :src="src" controls preload="none" class="media-audio"
                :title="text" @error="failed = true"></audio>
            <video v-else-if="resolvedModality === 'video'" :src="src" controls preload="metadata" class="media-video"
                :title="text" @error="failed = true"></video>
            <p class="media-path" :title="text">{{ text }}</p>
        </template>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme'
import { TEXT, sniffValueModality, isRemoteOrInline, mediaUrl } from '@/js/mediaModalities.js'

// Renders one table cell. Media cells (path strings to images/audio/video) are fetched
// through the backend media endpoints by (source, row, field) so the browser never needs
// direct filesystem access; http(s) URLs and data URIs are used as-is.
export default {
    props: {
        value: {
            default: null
        },
        field: {
            type: String,
            default: ''
        },
        row: {
            type: Number,
            default: 0
        },
        // column modality from /field_modalities; when absent the value is sniffed
        modality: {
            type: String,
            default: ''
        },
        // { type: 'dataset', datasetId } | { type: 'task', taskId, step, node }
        source: {
            type: Object,
            default: null
        }
    },
    data() {
        return {
            failed: false,
            expanded: false
        }
    },
    watch: {
        value() {
            this.failed = false
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme']),
        resolvedModality() {
            if (this.modality && this.modality !== TEXT) return this.modality
            // per-value sniff: a text column can still hold the odd path, and a media
            // column's empty cells must render as text
            return sniffValueModality(this.value)
        },
        text() {
            const v = this.value
            if (v === null || v === undefined || v === '') return ''
            if (typeof v === 'object') return JSON.stringify(v)
            return String(v)
        },
        src() {
            if (this.resolvedModality === TEXT) return null
            const first = Array.isArray(this.value) ? this.value[0] : this.value
            if (typeof first !== 'string' || !first) return null
            if (isRemoteOrInline(first)) return first
            return mediaUrl(this.source, this.row, this.field, 0)
        }
    },
}
</script>

<style lang="scss">
.media-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-width: 100%;
    overflow: hidden;

    .media-thumb {
        max-width: 120px;
        max-height: 80px;
        object-fit: contain;
        border-radius: 4px;
        cursor: zoom-in;

        &.expanded {
            max-width: 480px;
            max-height: 360px;
            cursor: zoom-out;
        }
    }

    .media-audio {
        width: 220px;
        height: 32px;
    }

    .media-video {
        max-width: 240px;
        max-height: 140px;
        border-radius: 4px;
    }

    .media-path {
        font-size: 11px;
        color: rgba(120, 120, 120, 0.9);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .media-error {
        font-size: 11px;
        color: rgba(200, 80, 80, 1);
    }
}
</style>
