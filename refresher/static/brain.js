/* Home's illustrative brain: pausable packets and arrival sparks, independent of source processing. */
'use strict';
(() => {
const graphMotion = document.getElementById('brain-motion');
const graphPause = document.getElementById('brain-pause');
if (!graphMotion || !graphPause) return;
function toggleBrainMotion() {
  const paused = graphMotion.closest('.home-welcome-art').classList.toggle('motion-paused');
  graphMotion.setAttribute('aria-label', paused ? 'Play animated brain knowledge graph' : 'Pause animated brain knowledge graph');
  graphPause.setAttribute('aria-label', paused ? 'Resume brain animation' : 'Pause brain animation');
  graphPause.querySelector('span').textContent = paused ? 'Resume animation' : 'Pause animation';
  graphPause.querySelector('path').setAttribute('d', paused ? 'M5 3l7 5-7 5z' : 'M5 3v10M11 3v10');
  syncBrainAnimation();
}
graphMotion.addEventListener('click', toggleBrainMotion);
graphPause.addEventListener('click', toggleBrainMotion);

const graphPanel = document.querySelector('.home-welcome-art');
graphPanel.classList.add('motion-dormant');
let graphVisible = false;
function updateGraphActivity() {
  graphPanel.classList.toggle('motion-dormant', !graphVisible || document.hidden);
  syncBrainAnimation();
}
new IntersectionObserver(entries => {
  graphVisible = entries[0].isIntersecting;
  updateGraphActivity();
}).observe(graphPanel);
document.addEventListener('visibilitychange', updateGraphActivity);

// Packet positions and node arrival effects share one clock; pausing freezes both.
const brainReducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const brainPackets = [...document.querySelectorAll('.brain-packet')].map(packet => {
  const route = document.getElementById(packet.dataset.route);
  const start = route.getPointAtLength(0);
  packet.setAttribute('transform', `translate(${start.x} ${start.y})`);
  return { packet, route, length: route.getTotalLength(),
    target: document.getElementById(packet.dataset.target),
    duration: Number(packet.dataset.duration), elapsed: Number(packet.dataset.offset) };
});
let brainFrame = null;
let brainLastTime = null;
function syncBrainAnimation() {
  const stopped = brainReducedMotion.matches || graphPanel.classList.contains('motion-paused') || graphPanel.classList.contains('motion-dormant');
  if (stopped) {
    if (brainFrame !== null) cancelAnimationFrame(brainFrame);
    brainFrame = null;
    brainLastTime = null;
  } else if (brainFrame === null) {
    brainFrame = requestAnimationFrame(animateBrain);
  }
}
function animateBrain(time) {
  const delta = brainLastTime === null ? 0 : Math.min(time - brainLastTime, 100);
  brainLastTime = time;
  for (const signal of brainPackets) {
    signal.elapsed += delta;
    if (signal.elapsed >= signal.duration) {
      signal.elapsed %= signal.duration;
      signal.target.classList.add('is-sparking');
    }
    const point = signal.route.getPointAtLength(signal.length * signal.elapsed / signal.duration);
    signal.packet.setAttribute('transform', `translate(${point.x} ${point.y})`);
  }
  brainFrame = requestAnimationFrame(animateBrain);
}
document.querySelectorAll('.brain-flare').forEach(flare => flare.addEventListener('animationend', () => {
  flare.closest('.brain-node').classList.remove('is-sparking');
}));
function reflectMotionPreference() {
  graphMotion.disabled = brainReducedMotion.matches;
  graphPause.disabled = brainReducedMotion.matches;
  const paused = graphPanel.classList.contains('motion-paused');
  const label = brainReducedMotion.matches ? 'Reduced motion enabled' : paused ? 'Resume animation' : 'Pause animation';
  graphPause.querySelector('span').textContent = label;
  graphPause.setAttribute('aria-label', label);
  graphMotion.setAttribute('aria-label', brainReducedMotion.matches ? 'Brain illustration: reduced motion enabled' : paused ? 'Play animated brain knowledge graph' : 'Pause animated brain knowledge graph');
  syncBrainAnimation();
}
brainReducedMotion.addEventListener('change', reflectMotionPreference);
reflectMotionPreference();
syncBrainAnimation();
})();
