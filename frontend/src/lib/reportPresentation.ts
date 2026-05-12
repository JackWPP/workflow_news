import type { DecisionTrace, ReportItem } from '../types'

const SOURCE_NAME_MAP: Record<string, string> = {
  'finance.sina.com.cn': '新浪财经',
  'k.sina.com.cn': '新浪看点',
  'sinopecnews.com.cn': '中国石化新闻网',
  'paper.sciencenet.cn': '科学网',
  'news.mit.edu': 'MIT News',
  'nature.com': 'Nature',
  'mdpi.com': 'MDPI',
  'plasticsnews.com': 'Plastics News',
  'ptonline.com': 'Plastics Technology',
  'plasticstoday.com': 'PlasticsToday',
  'kingfa.com.cn': '金发科技',
  'miit.gov.cn': '工信部',
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url.replace(/^https?:\/\//, '').split('/')[0]?.replace(/^www\./, '') || ''
  }
}

export function presentSourceName(rawName?: string | null, sourceUrl?: string | null) {
  const domain = extractDomain(sourceUrl || '')
  if (domain && SOURCE_NAME_MAP[domain]) return SOURCE_NAME_MAP[domain]
  const raw = rawName?.trim() || ''
  if (!raw) return domain || '未知来源'
  if (SOURCE_NAME_MAP[raw]) return SOURCE_NAME_MAP[raw]
  if (!raw.includes('.')) return raw
  return raw.replace(/^www\./, '')
}

export function trustLabel(trace?: DecisionTrace | null) {
  const tier = trace?.source_tier || ''
  if (tier === 'A') return '高信源'
  if (tier === 'B') return '可靠源'
  if (tier === 'C') return '辅助源'
  return '待核验'
}

export function evidenceLabel(trace?: DecisionTrace | null) {
  const strength = trace?.evidence_strength || ''
  if (strength === 'high') return '高证据'
  if (strength === 'medium') return '中证据'
  if (strength === 'low') return '低证据'
  if (strength === 'rss_embedded_digest') return 'RSS摘要'
  if (strength === 'rss') return 'RSS直连'
  return '待评估'
}

export function sourceKindLabel(trace?: DecisionTrace | null) {
  const kind = trace?.source_kind || ''
  const map: Record<string, string> = {
    government: '政府来源',
    official_company_newsroom: '企业官方',
    top_industry_media: '行业媒体',
    mainstream_media: '主流媒体',
    academic_journal: '学术期刊',
    vertical_media: '垂直媒体',
    academic: '高校研究',
    ai_rss_digest: '日报聚合',
  }
  return map[kind] || '一般来源'
}

export function sourceReliabilityLabel(trace?: DecisionTrace | null) {
  const label = trace?.source_reliability_label || ''
  const map: Record<string, string> = {
    official_source: '官方源',
    social_or_channel: '社交源',
    secondary_source: '整理源',
    rss_direct: 'RSS直连',
  }
  return map[label] || '来源待判定'
}

export function cardBrief(item: ReportItem) {
  const signal = item.research_signal?.trim()
  const summary = item.summary?.trim() || ''
  const base = signal && signal.length >= 10 ? signal : summary
  if (!base) return '本条由系统综合多个信号后入选。'
  const normalized = base.replace(/^Signal[:：]\s*/i, '').replace(/\s+/g, ' ').trim()
  const clipped = normalized.length > 88 ? `${normalized.slice(0, 88).trim()}…` : normalized
  return /[。！？.!?]$/.test(clipped) ? clipped : `${clipped}。`
}
