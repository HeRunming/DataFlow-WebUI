<template>
    <div class="table-wrapper" :class="[{ dark: theme == 'dark' }]">
        <i v-show="!tableInfo.length" class="empty-icon ms-Icon ms-Icon--Important"></i>
        <p v-show="!tableInfo.length" class="empty-title">{{ local('No Data') }}</p>
        <fv-details-list :theme="theme" v-show="tableInfo.length" :model-value="rows" :head="heads" ref="table"
            style="width: 100%; height: 100%">
            <template v-for="(col, i) in heads" :key="i + 1" v-slot:[`column_${i}`]="x">
                <p v-if="i == 0">{{ x.item.__row + 1 }}</p>
                <media-cell v-else :value="x.item[col.key]" :field="col.key" :row="x.item.__row"
                    :source="mediaSource"></media-cell>
            </template>
        </fv-details-list>
    </div>
</template>

<script>
import { mapState } from 'pinia'
import { useAppConfig } from '@/stores/appConfig'
import { useTheme } from '@/stores/theme';
import mediaCell from '../../common/mediaCell.vue'

export default {
    components: {
        mediaCell
    },
    props: {
        tableInfo: {
            type: Object,
            default: () => []
        },
        // { type: 'task', taskId, step, node } — lets media cells fetch referenced files
        mediaSource: {
            type: Object,
            default: null
        }
    },
    data() {
        return {
            heads: []
        }
    },
    watch: {
        tableInfo: {
            handler(newVal, oldVal) {
                if (newVal.length) this.getHeads()
            },
            deep: true
        }
    },
    computed: {
        ...mapState(useAppConfig, ['local']),
        ...mapState(useTheme, ['theme']),
        rows() {
            return this.tableInfo.map((record, idx) => ({ ...record, __row: idx }))
        }
    },
    mounted() {
        this.getHeads()
    },
    methods: {
        getHeads() {
            if (!this.tableInfo.length) return []
            let heads = []
            heads.push({
                key: 'index',
                content: '#',
                minWidth: 60,
                width: 60
            })
            for (let key in this.tableInfo[0]) {
                if (key === '__row') continue
                heads.push({
                    key,
                    content: key,
                    minWidth: 80,
                    width: 150,
                    sortName: key
                })
            }
            heads[heads.length - 1].width = 900
            this.heads = heads
            this.$nextTick(() => {
                this.$refs.table.headInit()
            })
        }
    }
}
</script>

<style lang="scss">
.table-wrapper {
    position: relative;
    width: 100%;
    height: 300px;
    padding: 5px;
    background: white;
    border: 1px solid rgba(120, 120, 120, 0.1);
    border-radius: 8px;
    box-shadow: 0px 1px 3px rgba(0, 0, 0, 0.1);
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    overflow: hidden;

    .dark {
        background: rgba(28, 30, 32, 1);
        border: 1px solid rgba(120, 120, 120, 0.1);
        box-shadow: 0px 1px 3px rgba(0, 0, 0, 0.1);
    }

    .empty-icon {
        font-size: 40px;
        color: rgba(120, 120, 120, 0.5);
    }

    .empty-title {
        font-size: 16px;
        color: rgba(120, 120, 120, 0.5);
    }
}
</style>
