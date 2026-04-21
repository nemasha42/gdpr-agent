/**
 * compare.js — Chart.js rendering, matrix search/sort/select, side-by-side panel.
 * Vanilla JS IIFE, no build step required.
 */
(function () {
    "use strict";

    // ── Colour palette ──────────────────────────────────────────────────────
    var C = {
        primary: "#0d6efd",
        purple:  "#6f42c1",
        green:   "#198754",
        yellow:  "#ffc107",
        red:     "#dc3545",
        gray:    "#6c757d",
        teal:    "#20c997",
    };

    // ── Read JSON blob ──────────────────────────────────────────────────────
    var dataEl = document.getElementById("compare-data");
    if (!dataEl) { return; }
    var compareData;
    try {
        compareData = JSON.parse(dataEl.textContent);
    } catch (e) {
        console.error("compare.js: failed to parse compare-data JSON", e);
        return;
    }

    var matrix      = compareData.matrix      || [];
    var sharedSPs   = compareData.shared_sps  || {};

    // ── Chart helpers ───────────────────────────────────────────────────────

    function makeChart(id, config) {
        var canvas = document.getElementById(id);
        if (!canvas) { return null; }
        return new Chart(canvas, config);
    }

    function commonOpts(extraPlugins) {
        return {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: Object.assign({
                legend: { display: false },
                tooltip: { mode: "index" },
            }, extraPlugins || {}),
            scales: {
                x: { beginAtZero: true },
                y: { ticks: { font: { size: 10 } } },
            },
        };
    }

    function stackedOpts(extraPlugins) {
        var opts = commonOpts(extraPlugins);
        opts.plugins.legend = { display: true, position: "bottom", labels: { font: { size: 10 } } };
        opts.scales.x.stacked = true;
        opts.scales.y.stacked = true;
        return opts;
    }

    // ── 1. SP Count bar chart ───────────────────────────────────────────────
    if (matrix.length) {
        var labels = matrix.map(function (r) { return r.company_name || r.domain; });

        makeChart("chart-sp-count", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "SP Count",
                    data: matrix.map(function (r) { return r.sp_count || 0; }),
                    backgroundColor: C.primary,
                }],
            },
            options: commonOpts(),
        });

        // ── 2. Jurisdiction stacked bar (adequate / risky / other) ──────────
        makeChart("chart-jurisdiction", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Adequate %",
                        data: matrix.map(function (r) { return r.adequate_pct || 0; }),
                        backgroundColor: C.green,
                    },
                    {
                        label: "Risky %",
                        data: matrix.map(function (r) { return r.risky_pct || 0; }),
                        backgroundColor: C.red,
                    },
                    {
                        label: "Other %",
                        data: matrix.map(function (r) {
                            var other = 100 - (r.adequate_pct || 0) - (r.risky_pct || 0);
                            return Math.max(0, other);
                        }),
                        backgroundColor: C.gray,
                    },
                ],
            },
            options: stackedOpts(),
        });

        // ── 3. Cross-border stacked bar (x-border / within-EU) ─────────────
        makeChart("chart-xborder", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Outside EU/EEA",
                        data: matrix.map(function (r) { return r.xborder_count || 0; }),
                        backgroundColor: C.red,
                    },
                    {
                        label: "Within EU/EEA",
                        data: matrix.map(function (r) {
                            return Math.max(0, (r.xborder_total || 0) - (r.xborder_count || 0));
                        }),
                        backgroundColor: C.green,
                    },
                ],
            },
            options: stackedOpts(),
        });

        // ── 4. Most Shared SPs bar ──────────────────────────────────────────
        var spDomains = Object.keys(sharedSPs).slice(0, 10);
        var spPcts    = spDomains.map(function (d) { return sharedSPs[d].pct || 0; });
        var spLabels  = spDomains.map(function (d) { return sharedSPs[d].name || d; });
        makeChart("chart-shared-sps", {
            type: "bar",
            data: {
                labels: spLabels,
                datasets: [{
                    label: "Used by % of seeds",
                    data: spPcts,
                    backgroundColor: C.purple,
                }],
            },
            options: commonOpts({
                legend: { display: false },
            }),
        });

        // ── 5. Lock-in stacked bar (locked / shared) ────────────────────────
        makeChart("chart-lockin", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Unique (locked-in) %",
                        data: matrix.map(function (r) { return r.lockin_pct || 0; }),
                        backgroundColor: C.yellow,
                    },
                    {
                        label: "Shared %",
                        data: matrix.map(function (r) {
                            return Math.max(0, 100 - (r.lockin_pct || 0));
                        }),
                        backgroundColor: C.teal,
                    },
                ],
            },
            options: stackedOpts(),
        });

        // ── 6. Geographic concentration bar ────────────────────────────────
        makeChart("chart-geo-concentration", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "Top country % of SPs",
                    data: matrix.map(function (r) { return r.geo_top_pct || 0; }),
                    backgroundColor: C.primary,
                }],
            },
            options: commonOpts(),
        });

        // ── 7. Transparency bar ─────────────────────────────────────────────
        makeChart("chart-transparency", {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "Transparency score",
                    data: matrix.map(function (r) { return r.transparency_score || 0; }),
                    backgroundColor: matrix.map(function (r) {
                        var s = r.transparency_score || 0;
                        return s >= 70 ? C.green : s >= 40 ? C.yellow : C.red;
                    }),
                }],
            },
            options: commonOpts(),
        });
    }

    // ── Matrix search ───────────────────────────────────────────────────────

    var searchInput   = document.getElementById("compare-search");
    var tbody         = document.getElementById("compare-tbody");
    var hiddenSummary = document.getElementById("compare-hidden-selected");
    var hiddenCount   = document.getElementById("hidden-selected-count");

    function applySearch() {
        if (!searchInput || !tbody) { return; }
        var q = searchInput.value.trim().toLowerCase();
        var rows = tbody.querySelectorAll("tr.compare-row");
        var hiddenSelected = 0;

        rows.forEach(function (row) {
            var nameCell = row.querySelector(".company-name");
            if (!nameCell) { return; }
            var text     = nameCell.textContent.trim();
            var textLow  = text.toLowerCase();
            var matches  = !q || textLow.indexOf(q) !== -1;
            row.style.display = matches ? "" : "none";

            // Track hidden-but-selected rows
            var cb = row.querySelector(".compare-checkbox");
            if (!matches && cb && cb.checked) { hiddenSelected++; }

            // Highlight matching text
            if (nameCell && q && matches) {
                var anchor = nameCell.querySelector("a");
                if (anchor) {
                    var idx = textLow.indexOf(q);
                    if (idx !== -1) {
                        var escaped = text.substring(0, idx)
                            + "<mark>" + text.substring(idx, idx + q.length) + "</mark>"
                            + text.substring(idx + q.length);
                        // Preserve favicon img before the anchor
                        var img = nameCell.querySelector("img");
                        var imgHtml = img ? img.outerHTML + " " : "";
                        nameCell.innerHTML = imgHtml + "<a href=\"" + anchor.href
                            + "\" class=\"text-decoration-none\">" + escaped + "</a>";
                    }
                }
            } else if (nameCell && !q) {
                // Restore clean text from data-name
                var anchorEl = nameCell.querySelector("a");
                if (anchorEl) {
                    var origText = row.dataset.name;
                    if (origText) {
                        // Capitalise first letter of each word as approximation
                        // The actual company_name is in the anchor's inner text
                        anchorEl.innerHTML = anchorEl.textContent;
                    }
                }
            }
        });

        if (hiddenSummary && hiddenCount) {
            if (hiddenSelected > 0) {
                hiddenCount.textContent = String(hiddenSelected);
                hiddenSummary.style.display = "";
            } else {
                hiddenSummary.style.display = "none";
            }
        }
    }

    if (searchInput) {
        searchInput.addEventListener("input", applySearch);
    }

    // ── Matrix sort ─────────────────────────────────────────────────────────

    var currentSort  = null;
    var currentOrder = "asc";

    function sortMatrix(field) {
        if (!tbody) { return; }
        if (currentSort === field) {
            currentOrder = currentOrder === "asc" ? "desc" : "asc";
        } else {
            currentSort  = field;
            currentOrder = "asc";
        }

        // Update arrow indicators
        document.querySelectorAll("#compare-matrix th[data-sort]").forEach(function (th) {
            var arrow = th.querySelector(".sort-arrow");
            if (!arrow) { return; }
            if (th.dataset.sort === field) {
                arrow.textContent = currentOrder === "asc" ? "▲" : "▼";
            } else {
                arrow.textContent = "";
            }
        });

        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr.compare-row"));
        rows.sort(function (a, b) {
            var av = a.dataset[field] || "";
            var bv = b.dataset[field] || "";

            // Numeric comparison if both parse as floats
            var an = parseFloat(av);
            var bn = parseFloat(bv);
            var numericA = !isNaN(an);
            var numericB = !isNaN(bn);
            var cmp;
            if (numericA && numericB) {
                cmp = an - bn;
            } else {
                cmp = av.localeCompare(bv);
            }
            return currentOrder === "asc" ? cmp : -cmp;
        });

        rows.forEach(function (row) { tbody.appendChild(row); });
    }

    document.querySelectorAll("#compare-matrix th[data-sort]").forEach(function (th) {
        th.style.cursor = "pointer";
        th.addEventListener("click", function () {
            sortMatrix(th.dataset.sort);
        });
    });

    // ── Checkboxes ──────────────────────────────────────────────────────────

    var compareBtn = document.getElementById("compare-btn");

    function getChecked() {
        if (!tbody) { return []; }
        return Array.prototype.slice.call(
            tbody.querySelectorAll(".compare-checkbox:checked")
        );
    }

    function updateCheckboxState() {
        var checked = getChecked();
        var count   = checked.length;

        if (compareBtn) {
            compareBtn.disabled = count < 2;
            compareBtn.textContent = count >= 2
                ? "Compare " + count + " Companies"
                : "Compare Selected";
        }

        // Highlight rows
        if (tbody) {
            tbody.querySelectorAll("tr.compare-row").forEach(function (row) {
                var cb = row.querySelector(".compare-checkbox");
                if (cb && cb.checked) {
                    row.style.background = "#f0f4ff";
                } else {
                    row.style.background = "";
                }
            });
        }

        // Disable unchecked checkboxes when 5 already selected
        if (tbody) {
            tbody.querySelectorAll(".compare-checkbox:not(:checked)").forEach(function (cb) {
                cb.disabled = count >= 5;
            });
        }
    }

    if (tbody) {
        tbody.addEventListener("change", function (e) {
            if (e.target && e.target.classList.contains("compare-checkbox")) {
                updateCheckboxState();
                applySearch(); // re-run to update hidden-selected count
            }
        });
    }

    // ── Side-by-side ────────────────────────────────────────────────────────

    var sideBySide = document.getElementById("side-by-side");

    function gradeClass(grade) {
        var map = { A: "bg-success", B: "bg-warning text-dark", C: "bg-danger", D: "bg-secondary" };
        return map[grade] || "bg-secondary";
    }

    function metricRow(label, value, unit) {
        unit = unit || "";
        return "<tr><td class=\"text-muted\" style=\"font-size:0.78rem\">" + label + "</td>"
            + "<td class=\"text-end fw-semibold\" style=\"font-size:0.82rem\">" + (value !== null && value !== undefined ? value : "—") + unit + "</td></tr>";
    }

    function renderSideBySide(data) {
        if (!sideBySide) { return; }
        var companies = data.companies || [];
        var overlap   = data.overlap   || {};

        var cols = companies.map(function (c) {
            var m = c.metrics || {};
            return "<div class=\"card\" style=\"min-width:200px;flex:1\">"
                + "<div class=\"card-header d-flex align-items-center justify-content-between\" style=\"background:#fafbfc\">"
                + "<span><img src=\"https://www.google.com/s2/favicons?domain=" + c.domain + "&sz=16\" width=\"14\" height=\"14\" class=\"me-1\">"
                + "<strong style=\"font-size:0.85rem\">" + (c.company_name || c.domain) + "</strong></span>"
                + "<span class=\"badge " + gradeClass(c.grade) + "\">" + c.grade + "</span>"
                + "</div>"
                + "<div class=\"card-body p-0\">"
                + "<table class=\"table table-sm mb-0\">"
                + metricRow("SPs",              m.sp_count)
                + metricRow("Categories",        m.category_count)
                + metricRow("Adequate",          m.adequate_pct, "%")
                + metricRow("Risky",             m.risky_pct, "%")
                + metricRow("Basis documented",  m.basis_pct, "%")
                + metricRow("Lock-in",           m.lockin_pct, "%")
                + metricRow("Cross-border",      m.xborder_count + "/" + m.xborder_total)
                + metricRow("Transparency",      m.transparency_score)
                + metricRow("Composite score",   m.composite_score)
                + "</table></div></div>";
        }).join("");

        // Shared SPs
        var sharedAll  = overlap.shared_by_all || [];
        var sharedSome = overlap.shared_by_some || {};
        var sharedAllHtml = sharedAll.length
            ? sharedAll.map(function (d) {
                return "<span class=\"badge bg-primary me-1 mb-1\" style=\"font-weight:400\">"
                    + "<img src=\"https://www.google.com/s2/favicons?domain=" + d + "&sz=12\" width=\"10\" height=\"10\" class=\"me-1\">"
                    + d + "</span>";
            }).join("")
            : "<span class=\"text-muted\" style=\"font-size:0.82rem\">None</span>";

        var sharedSomeKeys = Object.keys(sharedSome);
        var sharedSomeHtml = sharedSomeKeys.length
            ? sharedSomeKeys.map(function (d) {
                return "<span class=\"badge bg-secondary me-1 mb-1\" style=\"font-weight:400\">"
                    + d + " (" + sharedSome[d].join(", ") + ")</span>";
            }).join("")
            : "<span class=\"text-muted\" style=\"font-size:0.82rem\">None</span>";

        var html = "<div class=\"card mb-3\">"
            + "<div class=\"card-header\" style=\"background:#fafbfc\">"
            + "<strong>Side-by-Side Comparison</strong>"
            + " <span class=\"text-muted ms-2\" style=\"font-size:0.78rem\">"
            + overlap.total_unique + " unique SPs combined"
            + (overlap.combined_xborder_pct !== undefined
                ? " &middot; " + overlap.combined_xborder_pct + "% cross-border avg"
                : "")
            + "</span></div>"
            + "<div class=\"card-body\">"
            + "<div class=\"d-flex gap-3 flex-wrap mb-3\">" + cols + "</div>"
            + "<div><strong style=\"font-size:0.82rem\">Shared by all:</strong><div class=\"mt-1\">" + sharedAllHtml + "</div></div>"
            + (sharedSomeKeys.length
                ? "<div class=\"mt-2\"><strong style=\"font-size:0.82rem\">Shared by some:</strong><div class=\"mt-1\">" + sharedSomeHtml + "</div></div>"
                : "")
            + "</div></div>";

        sideBySide.innerHTML = html;
        sideBySide.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    if (compareBtn) {
        compareBtn.addEventListener("click", function () {
            var checked = getChecked();
            if (checked.length < 2) { return; }
            var domains = checked.map(function (cb) { return cb.value; });
            var url = "/api/compare/side-by-side?domains=" + encodeURIComponent(domains.join(","));
            compareBtn.disabled = true;
            compareBtn.textContent = "Loading…";

            fetch(url)
                .then(function (r) {
                    if (!r.ok) {
                        return r.json().then(function (err) { throw new Error(err.error || r.statusText); });
                    }
                    return r.json();
                })
                .then(function (data) {
                    renderSideBySide(data);
                })
                .catch(function (err) {
                    if (sideBySide) {
                        sideBySide.innerHTML = "<div class=\"alert alert-danger py-2\">" + err.message + "</div>";
                    }
                })
                .finally(function () {
                    updateCheckboxState();
                });
        });
    }

})();
