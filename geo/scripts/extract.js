// 在已加载页面的开发者控制台执行。只读取 DOM，不请求网络、不发题、不点击。
// 默认收集页面候选。可先选中回答文字，或设置 window.GEO_CAPTURE_SCOPE = {
//   answer: '#你已检查的回答选择器', references: '#你已检查的来源面板选择器'
// }。来源面板若未完整加载，仍保持 unknown，不凭外链数推断齐全。
// 控制台 copy 可用时复制 JSON；否则返回 JSON 字符串。另存新文件，保留旧原始记录。
// text/links/url/time 兼容旧字段。links 保留重复节点与原网址，绝不按顺序配正文引用。
(() => {
  const config = window.GEO_CAPTURE_SCOPE || {};
  const pick = value => typeof value === 'string' && value.trim() ? document.querySelector(value) : null;
  const answerRoot = pick(config.answer);
  const referenceRoot = pick(config.references);
  const selection = String(window.getSelection?.() || '').trim();
  const pageRoot = document.querySelector('main') || document.body;
  const pageText = pageRoot?.innerText || '';
  const text = answerRoot ? answerRoot.innerText : selection || pageText;
  const rawNodes = [...document.querySelectorAll('a[href]')];
  const links = rawNodes.map((a, nodeIndex) => ({
    t: (a.innerText || a.title || '').trim(),
    h: a.href,
    raw_href: a.getAttribute('href'),
    node_index: nodeIndex,
    scope: answerRoot?.contains(a) ? 'answer_candidate' :
      referenceRoot?.contains(a) ? 'reference_panel_candidate' : 'page_candidate'
  })).filter(l => /^https?:\/\//i.test(l.h));
  const data = {
    schema_version: 2,
    url: location.href,
    time: new Date().toISOString(),
    text,
    links,
    capture: {
      kind: answerRoot ? 'selected_answer_container' : selection ? 'selected_text' : 'page_candidates',
      answer_selector: answerRoot ? config.answer : null,
      reference_selector: referenceRoot ? config.references : null,
      selection_used: !answerRoot && Boolean(selection),
      page_text: pageText,
      reference_panel_text: referenceRoot?.innerText || null,
      complete: null,
      note: '只保留已加载 DOM；未确认正文完整、面板齐全或引用关系。请另写人工审查旁注。'
    },
    coverage: Object.fromEntries(['answer', 'reference_panel', 'landing_urls',
      'paragraph_map', 'source_archives'].map(k => [k, {
        state: 'unknown', captured: null, expected: null
      }])),
    citations: []
  };
  const output = JSON.stringify(data, null, 2);
  if (typeof copy === 'function') copy(output);
  return output;
})();
