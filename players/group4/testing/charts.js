const SVG = "http://www.w3.org/2000/svg";
export const fmt = new Intl.NumberFormat("en-US");
export const num = value => value == null ? "—" : fmt.format(value);

export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text != null) node.textContent = text;
  return node;
}

// Keep the highest and lowest observations in each bucket on long runs.
function plotPoints(values, limit = 900) {
  const stride = Math.max(1, Math.ceil(values.length / limit));
  const points = [[0, values[0]]];
  for (let start = 1; start < values.length; start += stride) {
    let min = start, max = start;
    for (let i = start + 1; i < Math.min(values.length, start + stride); i++) {
      if (values[i] < values[min]) min = i;
      if (values[i] > values[max]) max = i;
    }
    for (const day of [...new Set([min, max])].sort((a, b) => a - b)) {
      points.push([day, values[day]]);
    }
  }
  const last = values.length - 1;
  if (points.at(-1)[0] !== last) points.push([last, values[last]]);
  return points;
}

export function drawPopulation(container, legend, days, series) {
  const width = Math.max(270, Math.floor(container.clientWidth));
  const height = width < 420 ? 224 : 296;
  const left = 52, right = width - 20, top = 12, bottom = height - 36;
  const peak = series.reduce(
    (maximum, line) => line.history.reduce((value, count) => Math.max(value, count), maximum),
    4,
  );
  const step = Math.ceil(peak / 4);
  const x = day => left + (right - left) * day / days;
  const y = count => bottom - (bottom - top) * count / (step * 4);
  const chart = svg("svg", {
    viewBox: [0, 0, width, height].join(" "), class: "chart", role: "img",
    "aria-label": series.length
      ? "Population by day, " + series.map(line => line.label).join(" and ")
      : "Empty timeline with summer and winter bands",
  });

  // At long horizons the season bands become too narrow to distinguish.
  const seasonsVisible = days <= 7200;
  if (seasonsVisible) {
    for (let year = 0; year < days; year += 360) {
      for (const [start, name] of [[90, "summer"], [270, "winter"]]) {
        const from = year + start;
        const to = Math.min(from + 90, days);
        if (to <= from) continue;
        chart.append(svg("rect", {x: x(from), y: top, width: x(to) - x(from), height: bottom - top, class: name}));
      }
    }
  }

  for (let i = 0; i <= 4; i++) {
    const tick = y(i * step);
    chart.append(svg("line", {x1: left, x2: right, y1: tick, y2: tick, class: "axis"}));
    chart.append(svg("text", {x: left - 8, y: tick + 4, "text-anchor": "end"}, num(i * step)));
  }
  const tickCount = Math.min(width < 420 ? 2 : 5, days);
  for (let i = 0; i <= tickCount; i++) {
    const day = Math.round(days * i / tickCount);
    chart.append(svg("text", {x: x(day), y: bottom + 18, "text-anchor": "middle"}, num(day)));
  }
  chart.append(svg("text", {x: (left + right) / 2, y: height - 2, "text-anchor": "middle"}, "Day"));
  for (const line of series) {
    const points = plotPoints(line.history).map(([day, count]) => x(day).toFixed(2) + "," + y(count).toFixed(2));
    chart.append(svg("polyline", {points: points.join(" "), class: "series " + line.role}));
  }
  container.replaceChildren(chart);
  const keys = series.map(line => [line.role, line.label]);
  if (seasonsVisible) keys.push(["summer", "Summer feeding"], ["winter", "Winter breeding"]);
  legend.replaceChildren(...keys.map(([role, label]) => {
    const item = el("span", null);
    item.append(el("i", null, role), document.createTextNode(label));
    return item;
  }));
}
