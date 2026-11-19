document.addEventListener("DOMContentLoaded", () => {
    // Stepper elements
    const stepDash1 = document.getElementById("stepDash1");
    const stepDash2 = document.getElementById("stepDash2");
    const stepLabel = document.getElementById("stepLabel");

    // Section containers
    const captureSection = document.getElementById("captureSection");
    const initialCaptureOptions = document.getElementById("initialCaptureOptions");
    const previewContainer = document.getElementById("previewContainer");
    const selfiePreview = document.getElementById("selfiePreview");
    const openCameraBtn = document.getElementById("openCameraBtn");
    const fileInput = document.getElementById("fileInput");
    const nativeCameraInput = document.getElementById("nativeCameraInput");
    const retakeBtn = document.getElementById("retakeBtn");
    const searchBtn = document.getElementById("searchBtn");
    const errorBanner = document.getElementById("errorBanner");
    const errorMessage = document.getElementById("errorMessage");

    // Real-time Facial Analysis Elements
    const analysisBarFill = document.getElementById("analysisBarFill");
    const analysisStatusText = document.getElementById("analysisStatusText");
    const analysisPercentText = document.getElementById("analysisPercentText");

    // Duo / Multiple Faces Elements
    const duoDetectedBox = document.getElementById("duoDetectedBox");
    const faceChipsContainer = document.getElementById("faceChipsContainer");
    const searchTogetherBtn = document.getElementById("searchTogetherBtn");
    const searchEitherBtn = document.getElementById("searchEitherBtn");
    const singleActions = document.getElementById("singleActions");
    const duoResetAction = document.getElementById("duoResetAction");
    const retakeDuoBtn = document.getElementById("retakeDuoBtn");

    // Step 2 Loading (Radar Animation) Elements
    const loadingSection = document.getElementById("loadingSection");
    const userBannerAvatar = document.getElementById("userBannerAvatar");
    const loadingStatusText = document.getElementById("loadingStatusText");

    // Step 2 Results Elements
    const resultsSection = document.getElementById("resultsSection");
    const resultsBackBtn = document.getElementById("resultsBackBtn");
    const resultsCount = document.getElementById("resultsCount");
    const resultsSubtitle = document.getElementById("resultsSubtitle");
    const userBannerAvatarResults = document.getElementById("userBannerAvatarResults");
    const userMatchesCountText = document.getElementById("userMatchesCountText");
    const photoGrid = document.getElementById("photoGrid");
    const noteBanner = document.getElementById("noteBanner");
    const downloadZipBtn = document.getElementById("downloadZipBtn");
    const newSearchBtn = document.getElementById("newSearchBtn");

    // No Results Section
    const noResultsSection = document.getElementById("noResultsSection");
    const tryAgainBtn = document.getElementById("tryAgainBtn");

    // Live Camera Stream Modal
    const cameraModal = document.getElementById("cameraModal");
    const cameraVideo = document.getElementById("cameraVideo");
    const captureCanvas = document.getElementById("captureCanvas");
    const snapPhotoBtn = document.getElementById("snapPhotoBtn");
    const closeCameraBtn = document.getElementById("closeCameraBtn");

    // Lightbox Modal
    const lightboxModal = document.getElementById("lightboxModal");
    const lightboxImg = document.getElementById("lightboxImg");
    const lightboxFilename = document.getElementById("lightboxFilename");
    const lightboxDownloadBtn = document.getElementById("lightboxDownloadBtn");
    const closeLightboxBtn = document.getElementById("closeLightboxBtn");

    // Internal State
    let selectedImageBlob = null;
    let currentMatchedPhotoIds = [];
    let mediaStream = null;
    let detectedFacesData = [];
    let analysisTimer = null;

    function showError(msg) {
        if (errorMessage) errorMessage.textContent = msg;
        if (errorBanner) errorBanner.style.display = "flex";
    }

    function clearError() {
        if (errorBanner) errorBanner.style.display = "none";
    }

    // Fast client-side image compression
    function compressImageIfNeeded(file, maxDim = 1280) {
        return new Promise((resolve) => {
            if (!file || !file.type || !file.type.startsWith("image/")) {
                resolve(file);
                return;
            }
            if (file.size < 400 * 1024) {
                resolve(file);
                return;
            }
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    let w = img.width;
                    let h = img.height;
                    const max = Math.max(w, h);
                    if (max > maxDim) {
                        const s = maxDim / max;
                        w = Math.round(w * s);
                        h = Math.round(h * s);
                    }
                    const canvas = document.createElement("canvas");
                    canvas.width = w;
                    canvas.height = h;
                    const ctx = canvas.getContext("2d");
                    ctx.drawImage(img, 0, 0, w, h);
                    canvas.toBlob((blob) => {
                        resolve(blob || file);
                    }, "image/jpeg", 0.88);
                };
                img.onerror = () => resolve(file);
                img.src = e.target.result;
            };
            reader.onerror = () => resolve(file);
            reader.readAsDataURL(file);
        });
    }

    // High-speed, smooth sequential facial feature analysis animation:
    // Completes 0% -> 100% in 500ms, then STOPS the laser scan!
    function runFastAnalysisAnimation() {
        if (analysisTimer) clearInterval(analysisTimer);
        if (analysisBarFill) {
            analysisBarFill.style.width = "0%";
            analysisBarFill.style.background = "linear-gradient(90deg, #8a2038, #c92a4f, #ff6b8b)";
        }
        if (analysisPercentText) analysisPercentText.textContent = "0%";
        if (analysisStatusText) analysisStatusText.textContent = "Analyzing facial features...";

        const scanLaser = document.getElementById("scanLaserLine");
        const scanGrid = document.getElementById("scanGridOverlay");
        const scanPortal = document.getElementById("scanPortalContainer");
        const scanBadge = document.getElementById("scanVerifiedBadge");
        if (scanLaser) scanLaser.style.display = "block";
        if (scanGrid) scanGrid.style.display = "block";
        if (scanPortal) scanPortal.classList.remove("scanned");
        if (scanBadge) scanBadge.style.display = "none";

        // Button is immediately enabled so user never waits!
        if (searchBtn) {
            searchBtn.disabled = false;
        }

        const startTime = Date.now();
        const duration = 500; // 500ms total smooth progression

        analysisTimer = setInterval(() => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(100, Math.round((elapsed / duration) * 100));

            if (progress < 40) {
                if (analysisStatusText) analysisStatusText.textContent = "Analyzing facial features...";
            } else if (progress < 85) {
                if (analysisStatusText) analysisStatusText.textContent = "Validating face quality...";
            } else if (progress >= 100) {
                if (analysisStatusText) analysisStatusText.textContent = "Almost ready, face detected! ✅";
                if (analysisBarFill) analysisBarFill.style.width = "100%";
                if (analysisPercentText) analysisPercentText.textContent = "100%";

                // STOP scanning: laser line and grid overlay stop completely
                if (scanLaser) scanLaser.style.display = "none";
                if (scanGrid) scanGrid.style.display = "none";
                if (scanPortal) scanPortal.classList.add("scanned");
                if (scanBadge) scanBadge.style.display = "flex";

                clearInterval(analysisTimer);
                analysisTimer = null;
                return;
            } else {
                if (analysisStatusText) analysisStatusText.textContent = "Almost ready, face detected! ✅";
            }

            if (analysisBarFill) analysisBarFill.style.width = `${progress}%`;
            if (analysisPercentText) analysisPercentText.textContent = `${progress}%`;
        }, 18);
    }

    async function setPreview(blob) {
        selectedImageBlob = blob;
        const previewUrl = URL.createObjectURL(blob);
        selfiePreview.src = previewUrl;
        if (userBannerAvatar) userBannerAvatar.src = previewUrl;
        if (userBannerAvatarResults) userBannerAvatarResults.src = previewUrl;

        if (initialCaptureOptions) initialCaptureOptions.style.display = "none";
        previewContainer.style.display = "block";
        clearError();

        // Reset Duo elements
        if (duoDetectedBox) duoDetectedBox.style.display = "none";
        if (duoResetAction) duoResetAction.style.display = "none";
        if (singleActions) singleActions.style.display = "block";

        // Set stepper to Step 1
        if (stepDash1) stepDash1.classList.add("active");
        if (stepDash2) stepDash2.classList.remove("active");
        if (stepLabel) stepLabel.textContent = "STEP 1 OF 2";

        // Enable Find Matches IMMEDIATELY
        if (searchBtn) searchBtn.disabled = false;

        // Run fast visual analysis (0 to 100% in 500ms)
        runFastAnalysisAnimation();

        // Background check for Duo (Couple / Friends)
        const formData = new FormData();
        formData.append("file", blob, "preview.jpg");

        fetch("/api/guest/detect-faces", {
            method: "POST",
            body: formData
        }).then(res => res.json()).then(data => {
            if (data && data.success && data.faces && data.faces.length >= 2) {
                detectedFacesData = data.faces;
                if (singleActions) singleActions.style.display = "none";
                if (duoResetAction) duoResetAction.style.display = "block";
                if (duoDetectedBox) {
                    duoDetectedBox.style.display = "block";
                    renderFaceChips(data.faces);
                }
            }
        }).catch(err => {
            console.warn("Background face detection error:", err);
        });
    }

    function renderFaceChips(faces) {
        if (!faceChipsContainer) return;
        faceChipsContainer.innerHTML = "";

        faces.forEach((f, idx) => {
            const chip = document.createElement("div");
            chip.className = "face-chip";
            chip.title = `Search only for Person ${idx + 1}`;
            chip.innerHTML = `
                ${f.avatar_b64 ? `<img src="${f.avatar_b64}" alt="Face ${idx + 1}" class="face-chip-avatar">` : ""}
                <span class="face-chip-label">Person ${idx + 1}</span>
            `;
            chip.addEventListener("click", () => {
                executeSearch("single", f.index);
            });
            faceChipsContainer.appendChild(chip);
        });
    }

    function resetToCapture() {
        if (analysisTimer) clearInterval(analysisTimer);
        selectedImageBlob = null;
        detectedFacesData = [];
        if (fileInput) fileInput.value = "";
        if (nativeCameraInput) nativeCameraInput.value = "";
        if (initialCaptureOptions) initialCaptureOptions.style.display = "flex";
        if (previewContainer) previewContainer.style.display = "none";
        if (resultsSection) resultsSection.style.display = "none";
        if (noResultsSection) noResultsSection.style.display = "none";
        if (loadingSection) loadingSection.style.display = "none";
        if (captureSection) captureSection.style.display = "block";
        if (duoDetectedBox) duoDetectedBox.style.display = "none";
        if (singleActions) singleActions.style.display = "block";
        if (duoResetAction) duoResetAction.style.display = "none";

        if (stepDash1) stepDash1.classList.add("active");
        if (stepDash2) stepDash2.classList.remove("active");
        if (stepLabel) stepLabel.textContent = "STEP 1 OF 2";

        const scanLaser = document.getElementById("scanLaserLine");
        const scanGrid = document.getElementById("scanGridOverlay");
        const scanPortal = document.getElementById("scanPortalContainer");
        const scanBadge = document.getElementById("scanVerifiedBadge");
        if (scanLaser) scanLaser.style.display = "block";
        if (scanGrid) scanGrid.style.display = "block";
        if (scanPortal) scanPortal.classList.remove("scanned");
        if (scanBadge) scanBadge.style.display = "none";

        if (analysisBarFill) {
            analysisBarFill.style.width = "0%";
            analysisBarFill.style.background = "linear-gradient(90deg, #8a2038, #c92a4f, #ff6b8b)";
        }
        if (analysisPercentText) analysisPercentText.textContent = "0%";
        if (analysisStatusText) analysisStatusText.textContent = "Analyzing facial features...";

        clearError();
    }

    // Camera Handling (Live WebRTC stream)
    async function startCamera() {
        clearError();
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            try {
                mediaStream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: "user",
                        width: { ideal: 720 },
                        height: { ideal: 720 }
                    },
                    audio: false
                });
                cameraVideo.srcObject = mediaStream;
                cameraModal.style.display = "flex";
            } catch (err) {
                console.warn("Direct webcam access failed, falling back to native file camera:", err);
                nativeCameraInput.click();
            }
        } else {
            nativeCameraInput.click();
        }
    }

    function stopCamera() {
        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }
        cameraModal.style.display = "none";
    }

    if (snapPhotoBtn) {
        snapPhotoBtn.addEventListener("click", () => {
            const w = cameraVideo.videoWidth || 640;
            const h = cameraVideo.videoHeight || 480;
            captureCanvas.width = w;
            captureCanvas.height = h;

            const ctx = captureCanvas.getContext("2d");
            ctx.translate(w, 0);
            ctx.scale(-1, 1);
            ctx.drawImage(cameraVideo, 0, 0, w, h);

            captureCanvas.toBlob((blob) => {
                stopCamera();
                if (blob) {
                    setPreview(blob);
                }
            }, "image/jpeg", 0.90);
        });
    }

    if (openCameraBtn) openCameraBtn.addEventListener("click", startCamera);
    if (closeCameraBtn) closeCameraBtn.addEventListener("click", stopCamera);

    async function handleSelectedFile(file) {
        if (!file) return;
        const optimizedBlob = await compressImageIfNeeded(file, 1280);
        setPreview(optimizedBlob);
    }

    if (nativeCameraInput) {
        nativeCameraInput.addEventListener("change", (e) => {
            if (e.target.files && e.target.files[0]) {
                handleSelectedFile(e.target.files[0]);
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener("change", (e) => {
            if (e.target.files && e.target.files[0]) {
                handleSelectedFile(e.target.files[0]);
            }
        });
    }

    if (retakeBtn) retakeBtn.addEventListener("click", resetToCapture);
    if (retakeDuoBtn) retakeDuoBtn.addEventListener("click", resetToCapture);
    if (tryAgainBtn) tryAgainBtn.addEventListener("click", resetToCapture);
    if (newSearchBtn) newSearchBtn.addEventListener("click", resetToCapture);

    // Step 2 Back Button returns to Step 1 capture
    if (resultsBackBtn) {
        resultsBackBtn.addEventListener("click", () => {
            resultsSection.style.display = "none";
            noResultsSection.style.display = "none";
            loadingSection.style.display = "none";
            captureSection.style.display = "block";
            if (stepDash1) stepDash1.classList.add("active");
            if (stepDash2) stepDash2.classList.remove("active");
            if (stepLabel) stepLabel.textContent = "STEP 1 OF 2";
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    }

    // Search Execution (Step 2)
    async function executeSearch(mode = "single", faceIndex = null) {
        if (!selectedImageBlob) {
            showError("Please select or take a selfie first.");
            return;
        }

        clearError();

        // Switch Stepper to STEP 2 OF 2
        if (stepDash1) stepDash1.classList.add("active");
        if (stepDash2) stepDash2.classList.add("active");
        if (stepLabel) stepLabel.textContent = "STEP 2 OF 2";

        // Show concentric radar animation
        captureSection.style.display = "none";
        resultsSection.style.display = "none";
        noResultsSection.style.display = "none";
        loadingSection.style.display = "block";

        if (mode === "together") {
            loadingStatusText.textContent = "Finding photos where BOTH of you appear together...";
        } else if (mode === "either") {
            loadingStatusText.textContent = "Finding photos with either of you...";
        } else {
            loadingStatusText.textContent = "Matching facial embeddings across the collection...";
        }

        window.scrollTo({ top: 0, behavior: "smooth" });

        const formData = new FormData();
        formData.append("file", selectedImageBlob, "selfie.jpg");
        formData.append("mode", mode);
        if (faceIndex !== null) {
            formData.append("face_index", faceIndex.toString());
        }

        try {
            const res = await fetch("/api/guest/search", {
                method: "POST",
                body: formData
            });

            const data = await res.json();
            loadingSection.style.display = "none";

            if (!res.ok || !data.success) {
                captureSection.style.display = "block";
                if (stepDash2) stepDash2.classList.remove("active");
                if (stepLabel) stepLabel.textContent = "STEP 1 OF 2";
                showError(data.error || "Search failed. Please try again.");
                return;
            }

            if (data.count === 0) {
                noResultsSection.style.display = "block";
                return;
            }

            // Display results
            currentMatchedPhotoIds = data.photos.map(p => p.id);
            resultsCount.textContent = "AI Photo Results";
            resultsSubtitle.textContent = `${data.count} photo${data.count === 1 ? '' : 's'} matched`;
            userMatchesCountText.textContent = `${data.count} matches found`;

            if (data.note) {
                noteBanner.textContent = data.note;
                noteBanner.style.display = "flex";
            } else {
                noteBanner.style.display = "none";
            }

            renderPhotoGrid(data.photos);
            resultsSection.style.display = "block";

        } catch (err) {
            console.error("Search error:", err);
            loadingSection.style.display = "none";
            captureSection.style.display = "block";
            if (stepDash2) stepDash2.classList.remove("active");
            if (stepLabel) stepLabel.textContent = "STEP 1 OF 2";
            showError("Network or server error while searching photos. Please try again.");
        }
    }

    if (searchBtn) {
        searchBtn.addEventListener("click", () => executeSearch("single"));
    }

    if (searchTogetherBtn) {
        searchTogetherBtn.addEventListener("click", () => executeSearch("together"));
    }

    if (searchEitherBtn) {
        searchEitherBtn.addEventListener("click", () => executeSearch("either"));
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function renderPhotoGrid(photos) {
        photoGrid.innerHTML = "";
        photos.forEach(photo => {
            const card = document.createElement("div");
            card.className = "photo-card";
            const safeName = escapeHtml(photo.filename);
            const thumbUrl = photo.thumbnail_url || photo.url;
            const previewUrl = photo.preview_url || photo.url;
            const downloadUrl = photo.download_url || (photo.url.includes("?") ? `${photo.url}&download=true` : `${photo.url}?download=true`);

            card.innerHTML = `
                <div class="photo-img-wrapper" data-full-url="${previewUrl}" data-filename="${safeName}">
                    <img src="${thumbUrl}" alt="${safeName}" loading="eager" decoding="async" referrerpolicy="no-referrer" onerror="if(!this.dataset.retried){this.dataset.retried=1;this.src='/api/thumbnails/${photo.id}?proxy=true';}else{this.onerror=null;}">
                    <span class="score-badge">Match: ${photo.score}</span>
                </div>
                <a href="${downloadUrl}" class="download-icon-btn" download="${safeName}" title="Download high resolution photo" onclick="event.stopPropagation();">
                    ⬇️
                </a>
            `;

            card.querySelector(".photo-img-wrapper").addEventListener("click", () => {
                openLightbox(previewUrl, photo.filename, downloadUrl);
            });

            photoGrid.appendChild(card);
        });
    }

    // Lightbox Modal
    function openLightbox(url, filename, downloadUrl = null) {
        lightboxImg.src = url;
        lightboxFilename.textContent = filename;
        const dl = downloadUrl || (url.includes("?") ? `${url}&download=true` : `${url}?download=true`);
        lightboxDownloadBtn.href = dl;
        lightboxDownloadBtn.setAttribute("download", filename);
        lightboxModal.style.display = "flex";
    }

    if (closeLightboxBtn) {
        closeLightboxBtn.addEventListener("click", () => {
            lightboxModal.style.display = "none";
            lightboxImg.src = "";
        });
    }

    if (lightboxModal) {
        lightboxModal.addEventListener("click", (e) => {
            if (e.target === lightboxModal) {
                lightboxModal.style.display = "none";
                lightboxImg.src = "";
            }
        });
    }

    // Download All ZIP
    if (downloadZipBtn) {
        downloadZipBtn.addEventListener("click", async () => {
            if (!currentMatchedPhotoIds.length) return;

            downloadZipBtn.disabled = true;
            downloadZipBtn.textContent = "⏳ Zipping...";

            try {
                const res = await fetch("/api/guest/download-zip", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ photo_ids: currentMatchedPhotoIds })
                });

                if (!res.ok) throw new Error("ZIP creation failed");

                const blob = await res.blob();
                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = downloadUrl;
                a.download = "suv_event_photos.zip";
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(downloadUrl);
            } catch (err) {
                alert("Failed to download ZIP: " + err.message);
            } finally {
                downloadZipBtn.disabled = false;
                downloadZipBtn.textContent = "⬇️ All";
            }
        });
    }
});
