const SVG = 'http://www.w3.org/2000/svg';
const COLORS = {Free:'#cf423c', Pro:'#254cda', Enterprise:'#087860'};
const WINDOW = 5 * 60 * 1000;

function node(name, attributes, label) {
  const el = document.createElementNS(SVG, name);
  for (const [key,value] of Object.entries(attributes)) el.setAttribute(key, value);
  if (label) el.textContent = label;
  return el;
}

export class RequestTimeline {
  constructor(svg, count, empty) {
    this.svg = svg; this.count = count; this.empty = empty; this.records = [];
    new ResizeObserver(() => this.render()).observe(svg);
    this.render();
    setInterval(() => this.render(), 1000);
  }
  add(record) {
    const time = Date.parse(record.timestamp);
    if (!Number.isFinite(time)) return;
    this.records.push({...record,time});
    this.render();
  }
  render() {
    const now = Date.now(), start = now - WINDOW;
    this.records = this.records.filter(r => r.time >= start && r.time <= now + 1000).slice(-5000);
    this.svg.replaceChildren();
    const size = Math.max(280, this.svg.clientWidth || 1000);
    this.svg.setAttribute('viewBox', `0 0 ${size} 150`);
    const left = 16, width = size-32, baseline = 100, ticks = size < 500 ? 2 : 5;
    this.svg.append(node('line',{x1:left,y1:baseline,x2:left+width,y2:baseline,stroke:'#ccd7e6'}));
    for (let i=0; i<=ticks; i++) {
      const x = left + width * i/ticks;
      this.svg.append(node('line',{x1:x,y1:20,x2:x,y2:baseline,stroke:'#e5ebf3'}));
      this.svg.append(node('text',{x,y:128,'text-anchor': i===0?'start':i===ticks?'end':'middle',fill:'#61718b','font-size':11},new Date(start + WINDOW*i/ticks).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})));
    }
    const occupied = new Map();
    for (const record of this.records) {
      const x = left + Math.max(0,Math.min(1,(record.time-start)/WINDOW))*width;
      const bucket = Math.floor(x/8), lane = occupied.get(bucket) || 0;
      occupied.set(bucket, lane+1);
      const dot = node('circle',{cx:x,cy:baseline-5-(lane%10)*7,r:3.5,fill:COLORS[record.tier] || '#61718b','data-tier':record.tier || 'Unknown'});
      dot.append(node('title',{},`${new Date(record.time).toLocaleTimeString()} · ${record.tier || 'Unauthenticated'} · ${record.method} ${record.route} · ${record.status} · #${record.seq}`));
      this.svg.append(dot);
    }
    this.count.textContent = `${this.records.length.toLocaleString()} requests · Last 5 minutes`;
    this.empty.hidden = this.records.length > 0;
  }
}
