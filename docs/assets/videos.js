// Play the feature clips only while they are visible, and respect the
// operating system's reduced-motion setting. Clips are muted, so autoplay is
// allowed without user interaction.
(() => {
  const clips = Array.from(document.querySelectorAll("video.clip"));
  if (!clips.length) return;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (reduced.matches || !("IntersectionObserver" in window)) {
    clips.forEach((clip) => {
      clip.controls = true;
    });
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        const clip = entry.target;
        if (entry.isIntersecting) {
          const playing = clip.play();
          if (playing && playing.catch) {
            playing.catch(() => {
              clip.controls = true;
            });
          }
        } else {
          clip.pause();
        }
      });
    },
    { threshold: 0.4 }
  );

  clips.forEach((clip) => observer.observe(clip));
})();
