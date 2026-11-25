// A 5-second count-in before recording or playing (spec 5.7, 3.5): time to get your
// hands on the instrument. A short beep each second, higher on the last one, and no
// beep at the start itself, so it isn't recorded or played over the clip.

export const COUNTDOWN_SECONDS = 5;

let audio: AudioContext | null = null;

function beep(last: boolean) {
	try {
		audio ??= new AudioContext();
		if (audio.state === 'suspended') void audio.resume();
		const at = audio.currentTime;
		const osc = audio.createOscillator();
		const gain = audio.createGain();
		osc.frequency.value = last ? 1320 : 880;
		gain.gain.setValueAtTime(0.0001, at);
		gain.gain.exponentialRampToValueAtTime(0.3, at + 0.01);
		gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.13);
		osc.connect(gain).connect(audio.destination);
		osc.start(at);
		osc.stop(at + 0.15);
	} catch {
		// No sound is fine: the numbers still count down.
	}
}

/** Count down from `seconds`: `ontick(n)` for n = seconds…1, each with a beep, then
 * `ondone()`. Call it from a tap so the beeps may play. Returns a cancel function. */
export function countdown(ontick: (left: number) => void, ondone: () => void, seconds = COUNTDOWN_SECONDS): () => void {
	let left = seconds;
	ontick(left);
	beep(left === 1);
	const timer = setInterval(() => {
		left -= 1;
		if (left > 0) {
			ontick(left);
			beep(left === 1);
		} else {
			clearInterval(timer);
			ondone();
		}
	}, 1000);
	return () => clearInterval(timer);
}
