// dashboard/static/js/transfer-graph.js
// Transfer graph — D3.js v7 force-directed visualization
(function() {
  'use strict';

  const dataEl = document.getElementById('graph-data');
  if (!dataEl) return;

  const graphData = JSON.parse(dataEl.textContent);
  const { nodes, edges, stats } = graphData;

  // ── Coverage Donut ──────────────────────────────────────────────────────
  const donutEl = document.getElementById('coverage-donut');
  if (donutEl && stats.coverage_breakdown) {
    const cov = stats.coverage_breakdown;
    const donutData = [
      { label: 'ok', value: cov.ok || 0, color: '#198754' },
      { label: 'not_found', value: cov.not_found || 0, color: '#ffc107' },
      { label: 'error', value: cov.error || 0, color: '#dc3545' },
      { label: 'pending', value: cov.pending || 0, color: '#adb5bd' },
    ].filter(d => d.value > 0);

    const donutSvg = d3.select('#coverage-donut');
    const donutG = donutSvg.append('g').attr('transform', 'translate(40,40)');
    const arc = d3.arc().innerRadius(22).outerRadius(34);
    const pie = d3.pie().value(d => d.value).sort(null);

    donutG.selectAll('path')
      .data(pie(donutData))
      .join('path')
      .attr('d', arc)
      .attr('fill', d => d.data.color)
      .attr('stroke', 'white')
      .attr('stroke-width', 1);
  }

  const svg = d3.select('#transfer-graph');
  if (svg.empty()) return;

  const container = svg.node().parentElement;
  let width = container.clientWidth;
  let height = container.clientHeight;

  svg.attr('viewBox', [0, 0, width, height]);

  // Tooltip
  const tooltip = d3.select('body').append('div')
    .attr('class', 'graph-tooltip')
    .style('position', 'absolute')
    .style('background', 'white')
    .style('border', '1px solid #dee2e6')
    .style('border-radius', '6px')
    .style('padding', '8px 12px')
    .style('font-size', '0.78rem')
    .style('pointer-events', 'none')
    .style('opacity', 0)
    .style('z-index', 1000)
    .style('box-shadow', '0 2px 8px rgba(0,0,0,0.12)');

  // Zoom
  const g = svg.append('g');
  const zoom = d3.zoom()
    .scaleExtent([0.3, 4])
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // Risk → border color
  const riskColor = { adequate: '#198754', safeguarded: '#ffc107', risky: '#dc3545', unknown: '#adb5bd' };

  // Favicon / initials helpers
  const FALLBACK_COLORS = [
    '#e8d5f5', '#d5e8f5', '#d5f5e8', '#f5e8d5',
    '#f5d5e8', '#e8f5d5', '#d5d5f5', '#f5f5d5',
  ];

  function domainHash(domain) {
    let hash = 0;
    for (let i = 0; i < domain.length; i++) {
      hash = ((hash << 5) - hash) + domain.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash);
  }

  function getInitials(label) {
    const words = label.split(/[\s\-_.]+/).filter(Boolean);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return label.slice(0, 2).toUpperCase();
  }

  // Node sizing — by depth
  const _nodeSizes = { '-1': 24, 0: 22, 1: 17, 2: 14, 3: 12 };
  const _nodeOpacity = { '-1': 1, 0: 1, 1: 1, 2: 0.7, 3: 0.5 };

  function nodeSize(d) {
    if (d.depth !== undefined) return _nodeSizes[d.depth] || _nodeSizes[3];
    if (d.type === 'user') return 24;
    if (d.type === 'company') return 22;
    return 17;
  }
  function nodeOpacity(d) {
    if (d.depth !== undefined) return _nodeOpacity[d.depth] || _nodeOpacity[3];
    return 1;
  }

  // Layer x-targets — depth-based columns
  const _layerX = { '-1': 0.10, 0: 0.30, 1: 0.55, 2: 0.75, 3: 0.90 };
  function layerX(d) {
    if (d.depth !== undefined) return width * (_layerX[d.depth] || _layerX[3]);
    if (d.type === 'user') return width * 0.1;
    if (d.type === 'company') return width * 0.4;
    return width * 0.75;
  }

  // Edge style — structural, confirmed, or depth-faded dashed
  function edgeStyle(d) {
    if (d.type === 'structural') {
      return { stroke: '#4f7df9', width: 2, dasharray: 'none', opacity: 0.6 };
    }
    if (d.confirmed) {
      return { stroke: '#3fb950', width: 2, dasharray: 'none', opacity: 0.7 };
    }
    if ((d.depth || 0) <= 1) {
      return { stroke: '#8b949e', width: 1.5, dasharray: '4,3', opacity: 0.5 };
    }
    return { stroke: '#8b949e', width: 1, dasharray: '2,3', opacity: 0.35 };
  }

  // Build simulation
  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(80).strength(0.3))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('collision', d3.forceCollide().radius(d => nodeSize(d) + 4))
    .force('x', d3.forceX(d => layerX(d)).strength(0.4))
    .force('y', d3.forceY(height / 2).strength(0.05));

  // Draw edges — gradient stroke fades from source to target
  const link = g.append('g')
    .selectAll('line')
    .data(edges)
    .join('line')
    .attr('stroke', (d, i) => `url(#edge-grad-${i})`)
    .attr('stroke-width', d => edgeStyle(d).width)
    .attr('stroke-dasharray', d => edgeStyle(d).dasharray)
    .attr('opacity', d => edgeStyle(d).opacity);

  // Draw nodes
  const node = g.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('cursor', 'pointer')
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

  // User node — rounded rect with gradient; defs for clip paths too
  const defs = svg.append('defs');
  const grad = defs.append('linearGradient').attr('id', 'user-gradient')
    .attr('x1', '0%').attr('y1', '0%').attr('x2', '100%').attr('y2', '100%');
  grad.append('stop').attr('offset', '0%').attr('stop-color', '#0d6efd');
  grad.append('stop').attr('offset', '100%').attr('stop-color', '#6f42c1');

  // Per-edge gradients for directional fade (source → target)
  edges.forEach((e, i) => {
    const eStyle = edgeStyle(e);
    const color = e.confirmed ? eStyle.stroke : e.color;
    const grad = defs.append('linearGradient')
      .attr('id', `edge-grad-${i}`)
      .attr('gradientUnits', 'userSpaceOnUse');
    grad.append('stop').attr('offset', '0%')
      .attr('stop-color', color).attr('stop-opacity', 0.85);
    grad.append('stop').attr('offset', '70%')
      .attr('stop-color', color).attr('stop-opacity', 0.25);
    grad.append('stop').attr('offset', '100%')
      .attr('stop-color', color).attr('stop-opacity', 0.05);
  });

  // Glow filter for highlighting
  const filter = defs.append('filter').attr('id', 'glow');
  filter.append('feGaussianBlur').attr('stdDeviation', '2').attr('result', 'blur');
  const merge = filter.append('feMerge');
  merge.append('feMergeNode').attr('in', 'blur');
  merge.append('feMergeNode').attr('in', 'SourceGraphic');

  node.each(function(d) {
    const el = d3.select(this);
    const s = nodeSize(d);

    if (d.type === 'user') {
      // 48x48 rounded rect
      el.append('rect')
        .attr('x', -s).attr('y', -s)
        .attr('width', s * 2).attr('height', s * 2)
        .attr('rx', 12).attr('ry', 12)
        .attr('fill', 'url(#user-gradient)');
      el.append('text')
        .text('You')
        .attr('text-anchor', 'middle')
        .attr('dy', '0.35em')
        .attr('fill', 'white')
        .attr('font-size', '11px')
        .attr('font-weight', 700);
    } else if (d.type === 'company') {
      // 44x44 rounded rect
      if (d.fetched) {
        el.append('rect')
          .attr('x', -s).attr('y', -s)
          .attr('width', s * 2).attr('height', s * 2)
          .attr('rx', 10).attr('ry', 10)
          .attr('fill', '#0d6efd');
      } else {
        el.append('rect')
          .attr('x', -s).attr('y', -s)
          .attr('width', s * 2).attr('height', s * 2)
          .attr('rx', 10).attr('ry', 10)
          .attr('fill', 'transparent')
          .attr('stroke', '#adb5bd')
          .attr('stroke-width', 1)
          .attr('stroke-dasharray', '4,3');
      }

      // Favicon
      const compFavSize = 26;
      const compClipId = 'fc-' + d.id.replace(/[:.]/g, '-');
      defs.append('clipPath').attr('id', compClipId)
        .append('rect')
        .attr('x', -compFavSize / 2).attr('y', -compFavSize / 2)
        .attr('width', compFavSize).attr('height', compFavSize)
        .attr('rx', 4);

      const compFav = el.append('image')
        .attr('href', `https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`)
        .attr('x', -compFavSize / 2).attr('y', -compFavSize / 2)
        .attr('width', compFavSize).attr('height', compFavSize)
        .attr('clip-path', `url(#${compClipId})`);

      // Initials fallback (hidden)
      const compFallbackColor = FALLBACK_COLORS[domainHash(d.domain) % FALLBACK_COLORS.length];
      const compInitials = el.append('text')
        .text(getInitials(d.label))
        .attr('text-anchor', 'middle')
        .attr('dy', '0.35em')
        .attr('fill', d.fetched ? 'white' : '#6c757d')
        .attr('font-size', '14px')
        .attr('font-weight', 700)
        .style('display', 'none');

      compFav.on('error', function() {
        d3.select(this).style('display', 'none');
        compInitials.style('display', null);
        if (!d.fetched) {
          el.select('rect').attr('fill', compFallbackColor);
        }
      });
    } else {
      // Subprocessor — 34x34 rounded rect with risk border
      el.append('rect')
        .attr('x', -s).attr('y', -s)
        .attr('width', s * 2).attr('height', s * 2)
        .attr('rx', 8).attr('ry', 8)
        .attr('fill', 'white')
        .attr('stroke', riskColor[d.risk] || '#adb5bd')
        .attr('stroke-width', 1.5);

      // Favicon
      const spFavSize = 20;
      const spClipId = 'fc-' + d.id.replace(/[:.]/g, '-');
      defs.append('clipPath').attr('id', spClipId)
        .append('rect')
        .attr('x', -spFavSize / 2).attr('y', -spFavSize / 2)
        .attr('width', spFavSize).attr('height', spFavSize)
        .attr('rx', 3);

      const spFav = el.append('image')
        .attr('href', `https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`)
        .attr('x', -spFavSize / 2).attr('y', -spFavSize / 2)
        .attr('width', spFavSize).attr('height', spFavSize)
        .attr('clip-path', `url(#${spClipId})`);

      const spFallbackColor = FALLBACK_COLORS[domainHash(d.domain) % FALLBACK_COLORS.length];
      const spInitials = el.append('text')
        .text(getInitials(d.label))
        .attr('text-anchor', 'middle')
        .attr('dy', '0.35em')
        .attr('fill', '#495057')
        .attr('font-size', '12px')
        .attr('font-weight', 700)
        .style('display', 'none');

      spFav.on('error', function() {
        d3.select(this).style('display', 'none');
        spInitials.style('display', null);
        el.select('rect').attr('fill', spFallbackColor);
      });
    }
  });

  node.attr('opacity', d => nodeOpacity(d));

  // Tooltip on hover
  node.on('mouseover', function(event, d) {
    let html = `<strong>${d.label}</strong>`;
    if (d.domain) html += `<br><span style="color:#6c757d">${d.domain}</span>`;
    if (d.country) html += `<br>${d.country_code ? d.country_code + ' ' : ''}${d.country}`;
    if (d.risk) html += `<br>Risk: <span style="color:${riskColor[d.risk]}">${d.risk}</span>`;
    if (d.type === 'subprocessor' && d.sharing_count > 1) {
      html += `<br>Shared by ${d.sharing_count} companies`;
    }
    if (d.confirmed) html += '<br><span style="color:#3fb950">Confirmed by SAR</span>';
    if (d.service_category) {
      const lbl = (graphData.stats.service_category_labels || {})[d.service_category] || d.service_category.replace(/_/g, ' ');
      html += `<br>Category: ${lbl}`;
    }
    if (d.fetched_at) html += `<br>Fetched: ${d.fetched_at.split('T')[0]}`;
    if (d.stale) html += '<br><span style="color:#d29922">Data may be outdated</span>';
    tooltip.html(html)
      .style('left', (event.pageX + 12) + 'px')
      .style('top', (event.pageY - 12) + 'px')
      .transition().duration(150).style('opacity', 1);
  })
  .on('mousemove', function(event) {
    tooltip.style('left', (event.pageX + 12) + 'px')
      .style('top', (event.pageY - 12) + 'px');
  })
  .on('mouseout', function() {
    tooltip.transition().duration(150).style('opacity', 0);
  });

  // Simulation tick — update edge positions and gradient coordinates
  simulation.on('tick', () => {
    link.each(function(d, i) {
      d3.select(this)
        .attr('x1', d.source.x)
        .attr('y1', d.source.y)
        .attr('x2', d.target.x)
        .attr('y2', d.target.y);
      // Update gradient direction to match edge
      d3.select(`#edge-grad-${i}`)
        .attr('x1', d.source.x).attr('y1', d.source.y)
        .attr('x2', d.target.x).attr('y2', d.target.y);
    });
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Drag handlers
  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.1).restart();
    d.fx = d.x;
    d.fy = d.y;
  }
  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }

  // Zoom controls
  d3.select('#graph-zoom-in').on('click', () => svg.transition().call(zoom.scaleBy, 1.3));
  d3.select('#graph-zoom-out').on('click', () => svg.transition().call(zoom.scaleBy, 0.7));
  d3.select('#graph-reset').on('click', () => svg.transition().call(zoom.transform, d3.zoomIdentity));

  // Resize handler
  window.addEventListener('resize', () => {
    width = container.clientWidth;
    height = container.clientHeight;
    svg.attr('viewBox', [0, 0, width, height]);
    simulation.force('x', d3.forceX(d => layerX(d)).strength(0.4));
    simulation.force('y', d3.forceY(height / 2).strength(0.05));
    simulation.alpha(0.3).restart();
  });

  // ── Highlighting ─────────────────────────────────────────────────────────
  let activeHighlight = null;

  // Build adjacency index once for fast traversal
  const _adjForward = {};   // parentId → Set of childIds
  const _adjReverse = {};   // childId  → Set of parentIds
  edges.forEach(e => {
    const src = typeof e.source === 'object' ? e.source.id : e.source;
    const tgt = typeof e.target === 'object' ? e.target.id : e.target;
    if (!_adjForward[src]) _adjForward[src] = new Set();
    _adjForward[src].add(tgt);
    if (!_adjReverse[tgt]) _adjReverse[tgt] = new Set();
    _adjReverse[tgt].add(src);
  });

  const nodesById = {};
  nodes.forEach(n => { nodesById[n.id] = n; });

  function _collectChain(startId) {
    // Walk DOWN from startId to collect all descendants (SP → sub-SP → …)
    const visited = new Set();
    const queue = [startId];
    while (queue.length) {
      const cur = queue.shift();
      if (visited.has(cur)) continue;
      visited.add(cur);
      const children = _adjForward[cur];
      if (children) children.forEach(c => { if (!visited.has(c)) queue.push(c); });
    }
    // Walk UP from startId to reach user node (parent companies → user)
    const upQueue = [startId];
    while (upQueue.length) {
      const cur = upQueue.shift();
      if (visited.has(cur) && cur !== startId) continue;
      visited.add(cur);
      const parents = _adjReverse[cur];
      if (parents) parents.forEach(p => { if (!visited.has(p)) upQueue.push(p); });
    }
    return visited;
  }

  function _applyHighlight(chainNodes) {
    const chainEdges = new Set();
    edges.forEach(e => {
      const src = typeof e.source === 'object' ? e.source.id : e.source;
      const tgt = typeof e.target === 'object' ? e.target.id : e.target;
      if (chainNodes.has(src) && chainNodes.has(tgt)) chainEdges.add(e);
    });

    node.style('opacity', d => chainNodes.has(d.id) ? 1 : 0.1);
    link.style('opacity', d => chainEdges.has(d) ? 1 : 0.05)
        .attr('filter', d => chainEdges.has(d) ? 'url(#glow)' : null);
  }

  function highlightCompany(companyDomain) {
    const companyId = 'company:' + companyDomain;
    _applyHighlight(_collectChain(companyId));
    activeHighlight = companyDomain;
  }

  function highlightSubprocessor(spDomain) {
    // Try sp: first, fall back to company: (cross-linked nodes)
    const spId = _adjForward['sp:' + spDomain] || _adjReverse['sp:' + spDomain]
      ? 'sp:' + spDomain
      : 'company:' + spDomain;
    _applyHighlight(_collectChain(spId));
    activeHighlight = spDomain;
  }

  function clearHighlight() {
    node.style('opacity', 1);
    link.style('opacity', null).attr('filter', null);
    document.querySelectorAll('.company-accordion-item').forEach(el => {
      el.style.borderLeft = '';
    });
    activeHighlight = null;
  }

  // Click graph node → highlight + scroll to accordion
  node.on('click', function(event, d) {
    event.stopPropagation();
    if (d.type === 'company') {
      if (activeHighlight === d.domain) {
        clearHighlight();
        return;
      }
      highlightCompany(d.domain);
    } else if (d.type === 'subprocessor') {
      if (activeHighlight === d.domain) {
        clearHighlight();
        return;
      }
      highlightSubprocessor(d.domain);
    }
  });

  // Click background → reset
  svg.on('click', function(event) {
    if (event.target === svg.node()) {
      clearHighlight();
    }
  });

  // Escape key → reset
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') clearHighlight();
  });

  // ── Category filter ──────────────────────────────────────────────────────
  function filterByCategory(cat) {
    if (!cat) {
      node.attr('opacity', d => nodeOpacity(d));
      link.attr('opacity', d => edgeStyle(d).opacity);
      return;
    }
    node.attr('opacity', d => {
      if (d.type === 'user') return nodeOpacity(d);
      if (d.service_category === cat) return nodeOpacity(d);
      if (d.type === 'company') return 0.15;
      return 0.08;
    });
    link.attr('opacity', d => {
      if (d.type === 'structural') return 0.15;
      const tgt = typeof d.target === 'object' ? d.target : nodesById[d.target];
      if (tgt && tgt.service_category === cat) return edgeStyle(d).opacity;
      return 0.05;
    });
  }

  // ── Accordion → Graph integration ───────────────────────────────────────
  function panToNode(domain) {
    const companyId = 'company:' + domain;
    const nd = nodes.find(n => n.id === companyId);
    if (!nd) return;
    const scale = 1.2;
    const tx = width / 2 - nd.x * scale;
    const ty = height / 2 - nd.y * scale;
    svg.transition().duration(500)
      .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }

  // Expose for external integration
  window._transferGraph = {
    svg, g, node, link, nodes, edges, simulation, zoom, tooltip, riskColor,
    highlightCompany, highlightSubprocessor, clearHighlight, panToNode, filterByCategory,
  };
})();
