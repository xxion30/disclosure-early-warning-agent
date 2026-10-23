<script setup>
// 종목 상세 (R5 카드 3번). 종목 하나의 위험 상태를 공시 1건이 아니라 종목 단위로 본다.
// 데이터: GET /states/{stock_code} (R4, api/routes/states.py). ?mock=stages면 같은 모양의 예시 종목을 쓴다.
// 점수 숫자는 보여주지 않는다. 단계 배지, 항목 이름·설명, 근거 인용, DART 링크, 공시 타임라인만.
import { computed, onMounted, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import { mockMode } from '../api/mock'
import DisclosureTimeline from '../components/DisclosureTimeline.vue'
import StageBadge from '../components/StageBadge.vue'
import { formatTime, ITEM_GROUPS, itemStatus, missingLabel, STAGES, stagesOf, stageText } from '../labels'

const props = defineProps({ stockCode: { type: String, required: true } })

const state = ref(null)
const error = ref('')
const notFound = ref(false)
const acking = ref(false)
const ackedNow = ref(false)

async function load() {
  error.value = ''
  notFound.value = false
  ackedNow.value = false
  try {
    state.value = await api.getState(props.stockCode)
  } catch (err) {
    state.value = null
    // 내 담당 고객이 보유하지 않은 종목이거나 아직 판정이 없으면 404
    if (err.response?.status === 404) notFound.value = true
    else error.value = errorMessage(err)
  }
}
onMounted(load)
watch(() => props.stockCode, load)

// "확인함"은 읽음 표시일 뿐이다. 경보를 닫거나 승인하지 않는다. 지금 본 버전까지만 읽음으로 남긴다
async function ack() {
  acking.value = true
  error.value = ''
  try {
    const res = await api.ackState(props.stockCode, state.value.version)
    state.value.new = res.new
    ackedNow.value = true
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    acking.value = false
  }
}

const topStage = computed(() => stagesOf({ stage: state.value?.stage, stages: state.value?.active_stages })[0] || 'NONE')
const items = computed(() => state.value?.items || [])
// 위험 항목을 즉시 → 사건 → 재무 순으로 묶는다. 같은 묶음 안은 지금 반영 중인 것, 최근 것 먼저
const groups = computed(() => ['RED', 'ORANGE', 'YELLOW']
  .map((g) => ({
    group: g,
    items: items.value.filter((i) => i.group === g)
      .sort((a, b) => Number(!!b.active) - Number(!!a.active) || String(b.filed_at || '').localeCompare(String(a.filed_at || ''))),
  }))
  .filter((g) => g.items.length))
const activeItems = computed(() => items.value.filter((i) => i.active))
const missingItems = computed(() => items.value.filter((i) => i.missing?.length))
// 요약: 지금 반영 중인 항목 중 가장 최근 것
const latestActive = computed(() => [...activeItems.value].sort((a, b) => String(b.filed_at || '').localeCompare(String(a.filed_at || '')))[0] || null)

// 타임라인: API는 접수번호·제목·일자·링크만 준다. 위험 항목·해소 근거와 접수번호를 맞춰 종류와 색을 붙인다
const timeline = computed(() => {
  const resolutionNos = new Set((state.value?.resolution_evidence || []).map((e) => e.rcept_no))
  return (state.value?.timeline || []).map((t) => {
    const item = items.value.find((i) => i.rcept_no === t.rcept_no)
    const kind = t.kind || (resolutionNos.has(t.rcept_no) ? 'resolution' : item ? 'risk' : /정정/.test(t.title || '') ? 'correction' : 'other')
    return { ...t, kind, color: t.color || item?.group || null, url: t.url || dartUrl(t.rcept_no) }
  })
})
// 띠의 기준일: 위험 판정에 쓴 마지막 공시일과 타임라인의 가장 최근 공시일 중 늦은 날 (데모 공시가 오래전이어도 띠가 비지 않게)
const timelineEnd = computed(() => [state.value?.latest_filed_at, state.value?.timeline?.[0]?.filed_at].filter(Boolean).sort().pop() || null)

const dartUrl = (rcept) => (rcept && !state.value?.mock ? `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${encodeURIComponent(rcept)}` : null)
</script>

<template>
  <RouterLink to="/alerts" class="back-link">
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
    경보 목록
  </RouterLink>

  <p v-if="error" class="error" role="alert">{{ error }}</p>

  <section v-if="notFound" class="card empty-state">
    <h1>이 종목의 위험 상태를 볼 수 없어요</h1>
    <p class="muted">내 담당 고객이 보유한 종목이 아니거나, 아직 위험 판정이 없는 종목이에요.</p>
    <p v-if="!mockMode()" class="muted">화면만 먼저 보려면 주소에 <code>?mock=stages</code>를 붙여 예시 종목을 열어 보세요.</p>
  </section>

  <template v-if="state">
    <!-- 요약: 종목·단계·새 경보·확인함 -->
    <section class="card hero" aria-label="종목 요약">
      <div class="hero-head">
        <div class="hero-company">
          <span class="corp-mark" aria-hidden="true">{{ (state.corp_name || '-').slice(0, 1) }}</span>
          <div class="hero-copy">
            <div class="hero-title">
              <h1>{{ state.corp_name || state.stock_code }}</h1>
              <StageBadge all :stage="state.stage" :stages="state.active_stages" />
              <span v-if="state.new" class="new-pill">새 경보</span>
              <span v-if="state.resolved" class="badge tone-ok">해소됨</span>
            </div>
            <p class="hero-meta muted">
              <span>종목코드 {{ state.stock_code }}</span>
              <span v-if="state.updated_at">마지막 판정 {{ formatTime(state.updated_at) }}</span>
              <span>마지막 수집 {{ state.last_collection_at ? formatTime(state.last_collection_at) : '기록 없음' }}</span>
            </p>
          </div>
        </div>
        <div class="ack">
          <button v-if="state.new" type="button" class="ghost" :disabled="acking" @click="ack">
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10" /></svg>
            {{ acking ? '표시하는 중…' : '확인함' }}
          </button>
          <span v-else-if="ackedNow" class="ack-done" role="status">
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10" /></svg>
            확인했어요
          </span>
          <span v-if="state.new || ackedNow" class="ack-note">읽음 표시일 뿐, 경보를 닫지 않아요</span>
        </div>
      </div>

      <!-- 지금 상태 한 줄 요약 (숫자 카드 대신) -->
      <p class="state-line"><strong>지금 상태</strong><span>{{ STAGES[topStage].label }}<template v-if="STAGES[topStage].hint"> — {{ STAGES[topStage].hint }}</template></span></p>
      <ul class="summary-lines">
        <li v-if="latestActive">가장 최근 위험 근거: <strong>{{ latestActive.item }}</strong><template v-if="latestActive.filed_at"> ({{ latestActive.filed_at }})</template></li>
        <li>지금 판정에 반영 중인 항목 {{ activeItems.length }}개<template v-if="items.length > activeItems.length"> · 해소·기간 지난 항목 {{ items.length - activeItems.length }}개</template></li>
      </ul>
    </section>

    <!-- 사람이 확인해야 할 것 -->
    <div v-if="state.manual_check" class="banner warn" role="status">
      <span class="banner-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
      </span>
      <span class="banner-text">
        <strong>사람의 확인이 필요해요</strong>
        <span>판정에 필요한 값을 일부 찾지 못해 추측하지 않고 멈췄어요.<template v-if="missingItems.length"> 확인할 항목: {{ missingItems.map((i) => `${i.item}(${i.missing.map(missingLabel).join(', ')})`).join(', ') }}</template></span>
      </span>
    </div>

    <!-- 해소 -->
    <div v-if="state.resolved" class="banner ok" role="status">
      <span class="banner-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></svg>
      </span>
      <span class="banner-text">
        <strong>위험이 해소됐어요</strong>
        <span>확인된 해소 공시가 있어 해소로 표시해요. 이력은 그대로 남겨요.</span>
      </span>
    </div>
    <section v-if="state.resolution_evidence?.length" class="card" aria-label="해소 근거">
      <div class="card-head"><h2>해소 근거</h2></div>
      <figure v-for="(e, j) in state.resolution_evidence" :key="j" class="quote">
        <blockquote>“{{ e.quote }}”</blockquote>
        <figcaption>
          <span v-if="e.source" class="chip strong">{{ e.source }}</span>
          <a v-if="dartUrl(e.rcept_no)" :href="dartUrl(e.rcept_no)" target="_blank" rel="noopener">DART 원문 확인</a>
        </figcaption>
      </figure>
    </section>

    <!-- 위험 항목과 근거 -->
    <section class="card" aria-label="위험 항목과 근거">
      <div class="card-head">
        <h2>위험 항목과 근거</h2>
        <span class="muted section-note">단계는 규칙이 정하고, 화면에는 항목과 근거만 보여줘요</span>
      </div>
      <p v-if="!groups.length" class="muted">지금은 위험 항목이 없어요.</p>
      <div v-for="g in groups" :key="g.group" class="item-group">
        <h3 class="group-title"><span class="chip-dot" :class="g.group.toLowerCase()" aria-hidden="true"></span>{{ ITEM_GROUPS[g.group].label }} · {{ ITEM_GROUPS[g.group].name }}</h3>
        <article v-for="(it, k) in g.items" :key="it.event_id || k" class="risk-item" :class="{ inactive: !it.active }">
          <div class="risk-head">
            <strong>{{ it.item }}</strong>
            <span class="badge" :class="`tone-${itemStatus(it).tone}`">{{ itemStatus(it).text }}</span>
            <span v-if="it.filed_at" class="sub">{{ it.filed_at }}</span>
          </div>
          <p v-if="it.explanation" class="risk-text">{{ stageText(it.explanation) }}</p>
          <p v-if="it.missing?.length" class="notice warn">찾지 못한 값: {{ it.missing.map(missingLabel).join(', ') }} — 공시 원문을 직접 확인해 주세요.</p>
          <figure v-for="(e, j) in it.evidence" :key="j" class="quote">
            <blockquote>“{{ e.quote }}”</blockquote>
            <figcaption>
              <span v-if="e.source" class="chip strong">{{ e.source }}</span>
              <a v-if="dartUrl(e.rcept_no || it.rcept_no)" :href="dartUrl(e.rcept_no || it.rcept_no)" target="_blank" rel="noopener">DART 원문 확인</a>
            </figcaption>
          </figure>
          <p v-if="!it.evidence?.length" class="notice warn">근거 인용을 찾지 못했어요. 추측하지 않고 수동 확인으로 남겼어요.</p>
        </article>
      </div>
    </section>

    <details v-if="state.ai_explanation" class="card">
      <summary>AI 설명 보기</summary>
      <p class="explanation">{{ stageText(state.ai_explanation) }}</p>
      <p class="muted section-note">AI가 쓴 설명은 단계에 영향을 주지 않아요.</p>
    </details>

    <!-- 공시 타임라인 -->
    <section class="card" aria-label="공시 타임라인">
      <div class="card-head">
        <h2>공시 타임라인</h2>
        <span class="muted section-note">정정 공시는 같은 사건으로 묶여 반복으로 세지 않아요</span>
      </div>
      <DisclosureTimeline :timeline="timeline" :end-date="timelineEnd" />
    </section>
  </template>
</template>

<style scoped>
.back-link { min-height: 44px; }
.empty-state h1 { font-size: 22px; margin-bottom: 8px; }
.empty-state code, .muted code { background: var(--neutral-soft); padding: 1px 6px; border-radius: 6px; font-size: 13px; }
.hero { border-radius: var(--radius); padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.hero-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; flex-wrap: wrap; }
.hero-company { display: flex; align-items: flex-start; gap: 16px; flex: 1 1 480px; min-width: 0; }
.hero .corp-mark { width: 56px; height: 56px; border-radius: var(--radius-md); font-size: 22px; }
.hero-copy { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.hero-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hero-title h1 { margin: 0; overflow-wrap: anywhere; }
.hero-meta { display: flex; gap: 8px 12px; flex-wrap: wrap; margin: 0; font-size: 14px; }
.ack { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.ack-done { display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 16px; border-radius: var(--pill); background: var(--ok-soft); color: var(--ok); font-weight: 700; font-size: 14px; }
.ack-note { font-size: 12px; color: var(--muted); }
.explanation { margin: 0; font-size: 15px; line-height: 1.75; color: #2a3554; overflow-wrap: anywhere; white-space: pre-wrap; }
.state-line { margin: 0; display: flex; flex-direction: column; gap: 2px; font-size: 15px; line-height: 1.65; color: #2a3554; overflow-wrap: anywhere; }
.summary-lines { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: #2a3554; }
.section-note { font-size: 13px; }
.item-group { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
.item-group + .item-group { margin-top: 24px; }
.group-title { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 14px; color: var(--muted); font-weight: 700; }
.group-title .chip-dot { width: 10px; height: 10px; border-radius: 999px; }
.chip-dot.red { background: var(--critical); }
.chip-dot.orange { background: var(--warn-dot); }
.chip-dot.yellow { background: var(--caution-dot); }
.risk-item { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.risk-item.inactive { background: var(--surface-2); }
.risk-item.inactive .risk-text { color: var(--muted); }
.risk-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.risk-head strong { font-size: 16px; }
.risk-text { margin: 0; font-size: 15px; line-height: 1.65; color: #2a3554; overflow-wrap: anywhere; }
.quote .chip { white-space: normal; overflow-wrap: anywhere; }
.quote a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; }
details > summary { margin-bottom: 16px; }
details:not([open]) > summary { margin-bottom: 0; }
@media (max-width: 700px) {
  .ack { align-items: flex-start; }
}
</style>
