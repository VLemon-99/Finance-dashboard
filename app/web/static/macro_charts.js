// Draws the macro page's charts (Chart.js). Each <canvas data-chart="..."> carries its own
// dates, values, unit and decimals, prepared on the server (app/services/macro.py).
(function () {
  "use strict";
  if (typeof Chart === "undefined") return; // the cards still show their numbers

  var css = getComputedStyle(document.documentElement);
  var color = function (name) { return css.getPropertyValue(name).trim(); };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DOLLARS = { usd_millions: 1e6, usd_billions: 1e9, usd_trillions: 1e12 };

  function format(value, unit, decimals) {
    if (unit in DOLLARS) {
      var dollars = value * DOLLARS[unit];
      if (Math.abs(dollars) >= 1e12) return "$" + (dollars / 1e12).toFixed(2) + "T";
      if (Math.abs(dollars) >= 1e9) return "$" + (dollars / 1e9).toFixed(0) + "B";
      return "$" + (dollars / 1e6).toFixed(0) + "M";
    }
    var text = Number(value).toFixed(decimals);
    if (unit === "percent") return text + "%";
    if (unit === "ratio") return text + "×";
    return text;
  }

  function tick(value, unit) {
    if (unit in DOLLARS) return format(value, unit, 0);
    var text = String(Number(Number(value).toFixed(2)));
    if (unit === "percent") return text + "%";
    if (unit === "ratio") return text + "×";
    return text;
  }

  function dayLabel(iso) { // "2026-11-06" -> "Nov 6, 2026"
    var parts = iso.split("-");
    return MONTHS[Number(parts[1]) - 1] + " " + Number(parts[2]) + ", " + parts[0];
  }

  function monthLabel(iso) { // "2026-11-06" -> "Nov 2026"
    var parts = iso.split("-");
    return MONTHS[Number(parts[1]) - 1] + " " + parts[0];
  }

  document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
    var data;
    try { data = JSON.parse(canvas.getAttribute("data-chart")); } catch (e) { return; }
    var lineColor = color("--text") || "#1d1d1f";
    var gridColor = color("--border") || "#dedee3";
    var mutedColor = color("--muted") || "#6b6b70";
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          data: data.values,
          borderColor: lineColor,
          borderWidth: 1.5,
          pointRadius: data.values.length < 30 ? 2 : 0,
          pointHoverRadius: 3,
          tension: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) { return dayLabel(items[0].label); },
              label: function (item) { return format(item.parsed.y, data.unit, data.decimals); },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: mutedColor,
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 5,
              callback: function (value) { return monthLabel(this.getLabelForValue(value)); },
            },
          },
          y: {
            grid: {
              // The zero line stands out (e.g. an inverted yield curve is below it).
              color: function (ctx) { return ctx.tick && ctx.tick.value === 0 ? mutedColor : gridColor; },
            },
            ticks: {
              color: mutedColor,
              maxTicksLimit: 5,
              // Up to 2 decimals, without trailing zeros, so close ticks don't all read
              // the same (2.95%, 3%, 3.05% rather than 3.0%, 3.0%, 3.0%).
              callback: function (value) { return tick(value, data.unit); },
            },
          },
        },
      },
    });
  });
})();
