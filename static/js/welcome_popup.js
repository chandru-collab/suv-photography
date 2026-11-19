/**
 * SUV Photography Arakkonam - Welcome Popup Controller
 * Supports French Wine design, ambient music synthesis, and smooth navigation.
 */

(function () {
    let audioCtx = null;
    let isPlayingMusic = false;
    let musicInterval = null;

    // Web Audio Synthesizer for Festive Ambient Event Chimes
    function playAmbientChime(freq, duration = 1.8) {
        if (!audioCtx) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            audioCtx = new AudioContext();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }

        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        // Warm sine wave with subtle harmonics
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);

        // Exponential volume decay for bell / chime feel
        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.12, now + 0.05);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

        osc.connect(gain);
        gain.connect(audioCtx.destination);

        osc.start(now);
        osc.stop(now + duration);
    }

    // Pentatonic scale frequencies (Dreamy ambient celebratory chords)
    const notes = [261.63, 329.63, 392.00, 523.25, 659.25, 783.99]; // C4, E4, G4, C5, E5, G5

    function startAmbientMusic() {
        if (isPlayingMusic) return;
        isPlayingMusic = true;
        const btn = document.getElementById('suvMusicBtn');
        if (btn) btn.classList.add('playing');

        // Play initial chord
        playAmbientChime(notes[0], 2.2);
        setTimeout(() => playAmbientChime(notes[2], 2.0), 300);
        setTimeout(() => playAmbientChime(notes[3], 2.5), 600);

        // Loop gentle arpeggio every 3.2 seconds
        let step = 0;
        musicInterval = setInterval(() => {
            if (!isPlayingMusic) return;
            const n1 = notes[step % notes.length];
            const n2 = notes[(step + 2) % notes.length];
            playAmbientChime(n1, 2.2);
            setTimeout(() => playAmbientChime(n2, 2.0), 350);
            step = (step + 1) % notes.length;
        }, 3200);
    }

    function stopAmbientMusic() {
        isPlayingMusic = false;
        if (musicInterval) {
            clearInterval(musicInterval);
            musicInterval = null;
        }
        const btn = document.getElementById('suvMusicBtn');
        if (btn) btn.classList.remove('playing');
    }

    function toggleAmbientMusic() {
        if (isPlayingMusic) {
            stopAmbientMusic();
        } else {
            startAmbientMusic();
        }
    }

    function showWelcomePopup(force = false) {
        const overlay = document.getElementById('suvWelcomePopup');
        if (!overlay) return;

        // Check if already dismissed in this browser tab session unless forced
        if (!force && sessionStorage.getItem('suv_popup_dismissed') === 'true') {
            return;
        }

        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    function closeWelcomePopup() {
        const overlay = document.getElementById('suvWelcomePopup');
        if (!overlay) return;

        overlay.classList.remove('active');
        document.body.style.overflow = '';
        sessionStorage.setItem('suv_popup_dismissed', 'true');
        stopAmbientMusic();
    }

    function handleEnterGallery() {
        const isGalleryPage = window.location.pathname.startsWith('/gallery');
        if (isGalleryPage) {
            closeWelcomePopup();
        } else {
            closeWelcomePopup();
            const capture = document.getElementById('captureSection');
            if (capture) {
                capture.scrollIntoView({ behavior: 'smooth' });
            }
        }
    }

    function handleFindPhotos() {
        const isGuestPage = window.location.pathname === '/' || window.location.pathname.startsWith('/guest');
        if (isGuestPage) {
            closeWelcomePopup();
            // Scroll to selfie card if present
            const capture = document.getElementById('captureSection');
            if (capture) {
                capture.scrollIntoView({ behavior: 'smooth' });
            }
        } else {
            sessionStorage.setItem('suv_popup_dismissed', 'true');
            window.location.href = '/guest';
        }
    }

    window.initSuvWelcomePopup = function () {
        const overlay = document.getElementById('suvWelcomePopup');
        if (!overlay) return;

        const enterBtn = document.getElementById('suvEnterGalleryBtn');
        if (enterBtn) {
            enterBtn.addEventListener('click', handleEnterGallery);
        }

        const findBtn = document.getElementById('suvFindPhotosBtn');
        if (findBtn) {
            findBtn.addEventListener('click', handleFindPhotos);
        }

        const closeBtn = document.getElementById('suvClosePopupBtn');
        if (closeBtn) {
            closeBtn.addEventListener('click', closeWelcomePopup);
        }

        const musicBtn = document.getElementById('suvMusicBtn');
        if (musicBtn) {
            musicBtn.addEventListener('click', toggleAmbientMusic);
        }

        // Close on clicking backdrop (outside modal)
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) {
                closeWelcomePopup();
            }
        });

        // Close on ESC key
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay.classList.contains('active')) {
                closeWelcomePopup();
            }
        });

        // Auto display on page load
        setTimeout(() => {
            showWelcomePopup(false);
        }, 300);
    };

    window.openSuvWelcomePopup = function () {
        showWelcomePopup(true);
    };

    // Auto-init on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.initSuvWelcomePopup);
    } else {
        window.initSuvWelcomePopup();
    }
})();
