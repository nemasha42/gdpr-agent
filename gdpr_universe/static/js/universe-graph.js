// gdpr_universe/static/js/universe-graph.js
// GDPR Universe — D3.js v7 force-directed graph, dark-theme Bootstrap UI
// Adapted from dashboard/static/js/transfer-graph.js
(function() {
  'use strict';

  const dataEl = document.getElementById('graph-data');
  if (!dataEl) return;

  let graphData;
  try {
    graphData = JSON.parse(dataEl.textContent);
  } catch (e) {
    console.error('[universe-graph] JSON parse failed:', e);
    return;
  }
  const { nodes, edges, stats } = graphData;
  console.log('[universe-graph] data loaded:', nodes.length, 'nodes,', edges.length, 'edges');

  // Mode: "full" (dashboard) or "neighborhood" (company detail)
  const mode = dataEl.getAttribute('data-mode') || 'full';
  const centerDomain = dataEl.getAttribute('data-center') || null;

  const svg = d3.select('#universe-graph');
  if (svg.empty()) { console.error('[universe-graph] SVG element not found'); return; }

  const container = svg.node().parentElement;
  let width = container.clientWidth || container.getBoundingClientRect().width || 600;
  let height = container.clientHeight || container.getBoundingClientRect().height || 400;
  console.log('[universe-graph] container dims:', width, 'x', height);

  svg.attr('viewBox', [0, 0, width, height]);

  // ── Tooltip ──────────────────────────────────────────────────────────────
  const tooltip = d3.select('body').append('div')
    .attr('class', 'graph-tooltip')
    .style('position', 'absolute')
    .style('background', '#1a1d23')
    .style('border', '1px solid #30363d')
    .style('border-radius', '6px')
    .style('padding', '8px 12px')
    .style('font-size', '0.78rem')
    .style('color', '#c9d1d9')
    .style('pointer-events', 'none')
    .style('opacity', 0)
    .style('z-index', 1000)
    .style('box-shadow', '0 2px 8px rgba(0,0,0,0.4)');

  // ── Zoom ─────────────────────────────────────────────────────────────────
  const g = svg.append('g');
  const zoom = d3.zoom()
    .scaleExtent([0.3, 4])
    .filter(event => {
      // Disable wheel zoom so page scroll works — only zoom via buttons or pinch
      if (event.type === 'wheel') return false;
      return !event.button;
    })
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // ── Risk colors (dark-suited) ─────────────────────────────────────────────
  const riskColor = {
    adequate: '#3fb950',
    safeguarded: '#d29922',
    risky: '#f85149',
    unknown: '#8b949e',
  };

  // ── Favicon / initials helpers ────────────────────────────────────────────
  const FALLBACK_COLORS = [
    '#3d2f4f', '#2f3d4f', '#2f4f3d', '#4f3d2f',
    '#4f2f3d', '#3d4f2f', '#2f2f4f', '#4f4f2f',
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

  // ── Node sizing ───────────────────────────────────────────────────────────
  function nodeSize(d) {
    if (d.type === 'seed') return 22;
    // subprocessor depth-based sizing
    const depth = d.depth || 1;
    if (depth === 1) return 17;
    if (depth === 2) return 14;
    return 12;
  }

  function nodeOpacity(d) {
    if (d.type === 'seed') return 1;
    const depth = d.depth || 1;
    if (depth <= 1) return 1;
    if (depth === 2) return 0.7;
    return 0.5;
  }

  // ── x-force layering (full mode only) ────────────────────────────────────
  function layerX(d) {
    if (d.type === 'seed') return width * 0.25;
    const depth = d.depth || 1;
    if (depth === 1) return width * 0.60;
    return width * 0.85;
  }

  // ── Edge style ────────────────────────────────────────────────────────────
  function edgeStyle(d) {
    if (d.confirmed) {
      return { stroke: '#3fb950', width: 2, dasharray: 'none', opacity: 0.7 };
    }
    const depth = d.depth || 1;
    if (depth <= 1) {
      return { stroke: '#484f58', width: 1.5, dasharray: '4,3', opacity: 0.5 };
    }
    return { stroke: '#484f58', width: 1, dasharray: '2,3', opacity: 0.35 };
  }

  // ── Simulation ────────────────────────────────────────────────────────────
  let simulation;
  try {
    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(80).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('collision', d3.forceCollide().radius(d => nodeSize(d) + 4));
    console.log('[universe-graph] simulation started');
  } catch (e) {
    console.error('[universe-graph] simulation init failed:', e);
    return;
  }

  if (mode === 'neighborhood') {
    // Center force — no layering
    simulation
      .force('x', d3.forceX(width / 2).strength(d => d.is_center ? 0.8 : 0.05))
      .force('y', d3.forceY(height / 2).strength(d => d.is_center ? 0.8 : 0.05));
  } else {
    // Full mode — x layering by depth
    simulation
      .force('x', d3.forceX(d => layerX(d)).strength(0.4))
      .force('y', d3.forceY(height / 2).strength(0.05));
  }

  // ── Draw edges ────────────────────────────────────────────────────────────
  const link = g.append('g')
    .selectAll('line')
    .data(edges)
    .join('line')
    .attr('stroke', (d, i) => `url(#edge-grad-${i})`)
    .attr('stroke-width', d => edgeStyle(d).width)
    .attr('stroke-dasharray', d => edgeStyle(d).dasharray)
    .attr('opacity', d => edgeStyle(d).opacity);

  // ── Defs: gradients + clip paths + glow filter ────────────────────────────
  const defs = svg.append('defs');

  // Per-edge directional gradients
  edges.forEach((e, i) => {
    const eStyle = edgeStyle(e);
    const color = e.color || eStyle.stroke;
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

  // Glow filter for highlight
  const glowFilter = defs.append('filter').attr('id', 'glow');
  glowFilter.append('feGaussianBlur').attr('stdDeviation', '2').attr('result', 'blur');
  const merge = glowFilter.append('feMerge');
  merge.append('feMergeNode').attr('in', 'blur');
  merge.append('feMergeNode').attr('in', 'SourceGraphic');

  // ── Draw nodes ────────────────────────────────────────────────────────────
  const node = g.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('cursor', 'pointer')
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

  node.each(function(d) {
    const el = d3.select(this);
    const s = nodeSize(d);
    const isCenter = d.is_center || (centerDomain && d.domain === centerDomain);

    if (d.type === 'seed') {
      // Seed: larger rounded rect, dark fill or blue if center node
      const seedFill = isCenter ? '#0d6efd' : '#1a1d23';
      const seedStroke = isCenter ? '#58a6ff' : '#484f58';
      const seedStrokeWidth = isCenter ? 2.5 : 1.5;

      el.append('rect')
        .attr('x', -s).attr('y', -s)
        .attr('width', s * 2).attr('height', s * 2)
        .attr('rx', 10).attr('ry', 10)
        .attr('fill', seedFill)
        .attr('stroke', seedStroke)
        .attr('stroke-width', seedStrokeWidth);

      // Favicon
      const favSize = 26;
      const clipId = 'fc-' + d.id.replace(/[:.]/g, '-');
      defs.append('clipPath').attr('id', clipId)
        .append('rect')
        .attr('x', -favSize / 2).attr('y', -favSize / 2)
        .attr('width', favSize).attr('height', favSize)
        .attr('rx', 4);

      const fav = el.append('image')
        .attr('href', `https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`)
        .attr('x', -favSize / 2).attr('y', -favSize / 2)
        .attr('width', favSize).attr('height', favSize)
        .attr('clip-path', `url(#${clipId})`);

      const fallbackColor = FALLBACK_COLORS[domainHash(d.domain) % FALLBACK_COLORS.length];
      const initials = el.append('text')
        .text(getInitials(d.label))
        .attr('text-anchor', 'middle')
        .attr('dy', '0.35em')
        .attr('fill', '#c9d1d9')
        .attr('font-size', '14px')
        .attr('font-weight', 700)
        .style('display', 'none');

      fav.on('error', function() {
        d3.select(this).style('display', 'none');
        initials.style('display', null);
        el.select('rect').attr('fill', fallbackColor);
      });

    } else {
      // Subprocessor: smaller rounded rect with risk-colored border
      el.append('rect')
        .attr('x', -s).attr('y', -s)
        .attr('width', s * 2).attr('height', s * 2)
        .attr('rx', 8).attr('ry', 8)
        .attr('fill', '#1a1d23')
        .attr('stroke', riskColor[d.risk] || '#8b949e')
        .attr('stroke-width', 1.5);

      const favSize = 20;
      const clipId = 'fc-' + d.id.replace(/[:.]/g, '-');
      defs.append('clipPath').attr('id', clipId)
        .append('rect')
        .attr('x', -favSize / 2).attr('y', -favSize / 2)
        .attr('width', favSize).attr('height', favSize)
        .attr('rx', 3);

      const fav = el.append('image')
        .attr('href', `https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`)
        .attr('x', -favSize / 2).attr('y', -favSize / 2)
        .attr('width', favSize).attr('height', favSize)
        .attr('clip-path', `url(#${clipId})`);

      const fallbackColor = FALLBACK_COLORS[domainHash(d.domain) % FALLBACK_COLORS.length];
      const initials = el.append('text')
        .text(getInitials(d.label))
        .attr('text-anchor', 'middle')
        .attr('dy', '0.35em')
        .attr('fill', '#c9d1d9')
        .attr('font-size', '12px')
        .attr('font-weight', 700)
        .style('display', 'none');

      fav.on('error', function() {
        d3.select(this).style('display', 'none');
        initials.style('display', null);
        el.select('rect').attr('fill', fallbackColor);
      });
    }
  });

  node.attr('opacity', d => nodeOpacity(d));

  // ── Tooltip on hover ──────────────────────────────────────────────────────
  node.on('mouseover', function(event, d) {
    let html = `<strong style="color:#e6edf3">${d.label}</strong>`;
    if (d.domain) html += `<br><span style="color:#8b949e">${d.domain}</span>`;
    if (d.country) html += `<br><span style="color:#c9d1d9">${d.country}</span>`;
    if (d.risk) html += `<br>Risk: <span style="color:${riskColor[d.risk] || '#8b949e'}">${d.risk}</span>`;
    if (d.sharing_count > 1) {
      html += `<br><span style="color:#c9d1d9">Shared by ${d.sharing_count} companies</span>`;
    }
    if (d.service_category) {
      const lbl = d.service_category.replace(/_/g, ' ');
      html += `<br><span style="color:#8b949e">Category: ${lbl}</span>`;
    }
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

  // ── Simulation tick ───────────────────────────────────────────────────────
  simulation.on('tick', () => {
    link.each(function(d, i) {
      d3.select(this)
        .attr('x1', d.source.x)
        .attr('y1', d.source.y)
        .attr('x2', d.target.x)
        .attr('y2', d.target.y);
      d3.select(`#edge-grad-${i}`)
        .attr('x1', d.source.x).attr('y1', d.source.y)
        .attr('x2', d.target.x).attr('y2', d.target.y);
    });
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // ── Drag handlers ─────────────────────────────────────────────────────────
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

  // ── Zoom controls ─────────────────────────────────────────────────────────
  d3.select('#graph-zoom-in').on('click', () => svg.transition().call(zoom.scaleBy, 1.3));
  d3.select('#graph-zoom-out').on('click', () => svg.transition().call(zoom.scaleBy, 0.7));
  d3.select('#graph-reset').on('click', () => svg.transition().call(zoom.transform, d3.zoomIdentity));

  // ── Resize handler ────────────────────────────────────────────────────────
  window.addEventListener('resize', () => {
    width = container.clientWidth;
    height = container.clientHeight;
    svg.attr('viewBox', [0, 0, width, height]);
    if (mode === 'neighborhood') {
      simulation
        .force('x', d3.forceX(width / 2).strength(d => d.is_center ? 0.8 : 0.05))
        .force('y', d3.forceY(height / 2).strength(d => d.is_center ? 0.8 : 0.05));
    } else {
      simulation.force('x', d3.forceX(d => layerX(d)).strength(0.4));
      simulation.force('y', d3.forceY(height / 2).strength(0.05));
    }
    simulation.alpha(0.3).restart();
  });

  // ── Click interaction ─────────────────────────────────────────────────────
  if (mode === 'neighborhood') {
    // Neighborhood mode: click navigates to company detail
    node.on('click', function(event, d) {
      event.stopPropagation();
      window.location.href = `/company/${d.domain}`;
    });
  } else {
    // Full mode: click highlights chain (adjacency traversal), click again clears

    let activeHighlight = null;

    // Build adjacency index for fast traversal
    const _adjForward = {};  // id → Set of child ids
    const _adjReverse = {};  // id → Set of parent ids
    edges.forEach(e => {
      const src = typeof e.source === 'object' ? e.source.id : e.source;
      const tgt = typeof e.target === 'object' ? e.target.id : e.target;
      if (!_adjForward[src]) _adjForward[src] = new Set();
      _adjForward[src].add(tgt);
      if (!_adjReverse[tgt]) _adjReverse[tgt] = new Set();
      _adjReverse[tgt].add(src);
    });

    // Walk downstream only (parent → all SPs recursively)
    function _collectDownstream(startId) {
      const visited = new Set();
      const queue = [startId];
      while (queue.length) {
        const cur = queue.shift();
        if (visited.has(cur)) continue;
        visited.add(cur);
        const children = _adjForward[cur];
        if (children) children.forEach(c => { if (!visited.has(c)) queue.push(c); });
      }
      return visited;
    }

    // Walk both directions (full chain)
    function _collectChain(startId) {
      const visited = _collectDownstream(startId);
      // Walk up
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
      node.style('opacity', d => chainNodes.has(d.id) ? 1 : 0.08);
      link.style('opacity', d => chainEdges.has(d) ? 1 : 0.03)
          .attr('filter', d => chainEdges.has(d) ? 'url(#glow)' : null);
    }

    function clearHighlight() {
      node.style('opacity', null).attr('opacity', d => nodeOpacity(d));
      link.style('opacity', null).attr('filter', null);
      activeHighlight = null;
    }

    // Highlight a single node + its full downstream SP chain
    function highlightDomain(domain) {
      const n = nodes.find(n => n.domain === domain);
      if (!n) return;
      if (activeHighlight === n.id) { clearHighlight(); return; }
      _applyHighlight(_collectDownstream(n.id));
      activeHighlight = n.id;
    }

    // Highlight multiple domains + union of all their downstream chains
    function highlightDomains(domains) {
      const allNodes = new Set();
      domains.forEach(domain => {
        const n = nodes.find(n => n.domain === domain);
        if (n) {
          _collectDownstream(n.id).forEach(id => allNodes.add(id));
        }
      });
      if (allNodes.size === 0) { clearHighlight(); return; }
      _applyHighlight(allNodes);
      activeHighlight = '__multi__';
    }

    node.on('click', function(event, d) {
      event.stopPropagation();
      if (activeHighlight === d.id) {
        clearHighlight();
        return;
      }
      _applyHighlight(_collectDownstream(d.id));
      activeHighlight = d.id;
    });

    svg.on('click', function(event) {
      if (event.target === svg.node()) {
        clearHighlight();
      }
    });

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') clearHighlight();
    });

    // Expose for external integration
    window._universeGraph = {
      svg, g, node, link, nodes, edges, simulation, zoom, tooltip, riskColor,
      clearHighlight, highlightDomain, highlightDomains,
    };
  }

})();
