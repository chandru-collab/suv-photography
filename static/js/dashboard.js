document.addEventListener("DOMContentLoaded", () => {
    // Elements
    const logoutBtn = document.getElementById("logoutBtn");
    const copyUrlBtn = document.getElementById("copyUrlBtn");
    const guestUrlInput = document.getElementById("guestUrlInput");
    const baseUrlInput = document.getElementById("baseUrlInput");
    const updateUrlBtn = document.getElementById("updateUrlBtn");
    const qrImage = document.getElementById("qrImage");

    const driveSyncForm = document.getElementById("driveSyncForm");
    const driveUrl = document.getElementById("driveUrl");
    const startSyncBtn = document.getElementById("startSyncBtn");
    const syncProgressContainer = document.getElementById("syncProgressContainer");
    const syncActionText = document.getElementById("syncActionText");
    const syncPercentText = document.getElementById("syncPercentText");
    const syncProgressBar = document.getElementById("syncProgressBar");
    const syncFound = document.getElementById("syncFound");
    const syncAdded = document.getElementById("syncAdded");
    const syncSkipped = document.getElementById("syncSkipped");
    const syncFaces = document.getElementById("syncFaces");
    const syncResultBanner = document.getElementById("syncResultBanner");

    const dropzone = document.getElementById("dropzone");
    const directUploadInput = document.getElementById("directUploadInput");
    const uploadStatus = document.getElementById("uploadStatus");

    const settingsForm = document.getElementById("settingsForm");
    const eventNameInput = document.getElementById("eventNameInput");
    const coupleTitleInput = document.getElementById("coupleTitleInput");
    const eventDateInput = document.getElementById("eventDateInput");
    const eventLocationInput = document.getElementById("eventLocationInput");
    const coverPhotoFileInput = document.getElementById("coverPhotoFileInput");
    const coverPhotoPreview = document.getElementById("coverPhotoPreview");
    const coverPhotoStatus = document.getElementById("coverPhotoStatus");
    const resetCoverPhotoBtn = document.getElementById("resetCoverPhotoBtn");
    const thresholdSlider = document.getElementById("thresholdSlider");
    const thresholdVal = document.getElementById("thresholdVal");
    const navEventName = document.getElementById("navEventName");

    const statTotalPhotos = document.getElementById("statTotalPhotos");
    const statTotalFaces = document.getElementById("statTotalFaces");
    const statMultiFaces = document.getElementById("statMultiFaces");
    const statThreshold = document.getElementById("statThreshold");

    const refreshPhotosBtn = document.getElementById("refreshPhotosBtn");
    const photosList = document.getElementById("photosList");
    const photosLoading = document.getElementById("photosLoading");
    const photosEmpty = document.getElementById("photosEmpty");

    let syncPollTimer = null;

    // Logout
    logoutBtn.addEventListener("click", async () => {
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.href = "/login";
    });

    // Copy URL
    copyUrlBtn.addEventListener("click", () => {
        guestUrlInput.select();
        navigator.clipboard.writeText(guestUrlInput.value);
        const originalText = copyUrlBtn.textContent;
        copyUrlBtn.textContent = "✅ Copied!";
        setTimeout(() => { copyUrlBtn.textContent = originalText; }, 2000);
    });

    // Update Base URL & Regenerate QR (if custom URL input exists)
    if (updateUrlBtn && baseUrlInput) {
        updateUrlBtn.addEventListener("click", async () => {
            const val = baseUrlInput.value.trim();
            if (!val) {
                alert("Please enter a valid base URL (e.g. http://192.168.1.10:8000)");
                return;
            }

            updateUrlBtn.disabled = true;
            updateUrlBtn.textContent = "Updating...";

            try {
                const res = await fetch("/api/dashboard/qr/regenerate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ base_url: val })
                });

                const data = await res.json();
                if (data.success) {
                    qrImage.src = data.qr_image;
                    guestUrlInput.value = data.guest_url;
                    alert("QR code updated successfully!");
                } else {
                    alert("Error: " + (data.error || "Failed to update QR code"));
                }
            } catch (err) {
                alert("Network error updating QR code");
            } finally {
                updateUrlBtn.disabled = false;
                updateUrlBtn.textContent = "Update QR";
            }
        });
    }

    const switchFolderBtn = document.getElementById("switchFolderBtn");
    const cancelSyncBtn = document.getElementById("cancelSyncBtn");

    // Fast Switch Folder Instantly (Zero delay, no waiting for sync)
    if (switchFolderBtn) {
        switchFolderBtn.addEventListener("click", async () => {
            const url = driveUrl.value.trim();
            if (!url) {
                showSyncResult("error", "Please paste a Google Drive folder link or folder ID.");
                return;
            }

            switchFolderBtn.disabled = true;
            switchFolderBtn.textContent = "⚡ Switching...";

            try {
                const res = await fetch("/api/drive/switch-folder", {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ folder_url: url, auto_sync: true, clear_previous: true })
                });

                const data = await res.json().catch(() => null);
                if (res.ok && data && data.success) {
                    showSyncResult("success", `Active event switched to "${data.folder_name}". Opening gallery...`);
                    if (navEventName) navEventName.textContent = data.folder_name;
                    setTimeout(() => {
                        window.open(data.redirect_url, "_blank");
                    }, 500);
                } else {
                    const err = (data && (data.error || data.detail)) || "Failed to switch folder";
                    showSyncResult("error", err);
                }
            } catch (err) {
                showSyncResult("error", "Network error switching folder: " + err.message);
            } finally {
                switchFolderBtn.disabled = false;
                switchFolderBtn.textContent = "⚡ Switch & View Gallery (Instant)";
            }
        });
    }

    // Cancel Sync
    if (cancelSyncBtn) {
        cancelSyncBtn.addEventListener("click", async () => {
            cancelSyncBtn.disabled = true;
            cancelSyncBtn.textContent = "Stopping...";
            try {
                await fetch("/api/sync/cancel", { method: "POST", credentials: "include" });
                if (syncPollTimer) clearInterval(syncPollTimer);
                syncProgressContainer.style.display = "none";
                startSyncBtn.disabled = false;
                startSyncBtn.textContent = "🔄 Fast Face Indexing (Parallel)";
                showSyncResult("info", "Sync stopped. You can now switch folders or restart anytime.");
            } catch (e) {
                console.error("Cancel sync error:", e);
            } finally {
                cancelSyncBtn.disabled = false;
                cancelSyncBtn.textContent = "⏹️ Cancel Sync";
            }
        });
    }

    // Copy Service Account Email
    const copyServiceEmailBtn = document.getElementById("copyServiceEmailBtn");
    if (copyServiceEmailBtn) {
        copyServiceEmailBtn.addEventListener("click", () => {
            const email = "suv-photography@suv-photography-514705.iam.gserviceaccount.com";
            navigator.clipboard.writeText(email).then(() => {
                copyServiceEmailBtn.textContent = "✅ Copied!";
                setTimeout(() => { copyServiceEmailBtn.textContent = "📋 Copy"; }, 2000);
            }).catch(() => {
                copyServiceEmailBtn.textContent = "✅ suv-photography@...";
            });
        });
    }

    // Test & Verify Folder Access Diagnostic
    const testDriveAccessBtn = document.getElementById("testDriveAccessBtn");
    const testAccessResult = document.getElementById("testAccessResult");
    if (testDriveAccessBtn) {
        testDriveAccessBtn.addEventListener("click", async () => {
            const rawUrl = driveUrl.value.trim();
            if (!rawUrl) {
                testAccessResult.style.display = "block";
                testAccessResult.style.background = "#fff3cd";
                testAccessResult.style.border = "1px solid #ffeeba";
                testAccessResult.style.color = "#856404";
                testAccessResult.innerHTML = "⚠️ Please enter a Google Drive link or click 'Use Verified Folder: \"Hi\"'.";
                return;
            }

            testDriveAccessBtn.disabled = true;
            testDriveAccessBtn.textContent = "⏳ Testing Access...";
            testAccessResult.style.display = "block";
            testAccessResult.style.background = "#f0f4f8";
            testAccessResult.style.border = "1px solid #d0d7de";
            testAccessResult.style.color = "#24292f";
            testAccessResult.innerHTML = "Connecting to Google Drive API...";

            try {
                const res = await fetch(`/api/drive/check-access?folder_url=${encodeURIComponent(rawUrl)}`, {
                    credentials: "include"
                });
                const data = await res.json();

                if (res.ok && data.success) {
                    testAccessResult.style.background = "#e6f9ed";
                    testAccessResult.style.border = "1px solid #a3e6b7";
                    testAccessResult.style.color = "#155724";
                    const samples = (data.sample_files || []).slice(0, 4).join(", ");
                    testAccessResult.innerHTML = `
                        <strong>✅ Access Verified!</strong><br>
                        📁 Folder Name: <strong>${data.folder_name}</strong><br>
                        📸 Files Found: <strong>${data.file_count}</strong> (${samples}...)<br>
                        🔑 Service Account: <code>${data.service_account}</code><br>
                        Ready to sync! Click <strong>Sync Photos from Google Drive</strong> below.
                    `;
                } else {
                    testAccessResult.style.background = "#f8d7da";
                    testAccessResult.style.border = "1px solid #f5c6cb";
                    testAccessResult.style.color = "#721c24";
                    testAccessResult.innerHTML = `
                        <strong>❌ Folder Not Accessible by Service Account</strong><br>
                        ${data.hint || "Make sure the folder is shared with:"}<br>
                        <code style="user-select: all; font-weight: bold; background: #fff; padding: 2px 6px; border-radius: 4px;">suv-photography@suv-photography-514705.iam.gserviceaccount.com</code><br>
                        <em>Tip: In Google Drive, right-click the folder &rarr; Share &rarr; paste the email above with "Viewer" access.</em>
                    `;
                }
            } catch (err) {
                testAccessResult.style.background = "#f8d7da";
                testAccessResult.style.border = "1px solid #f5c6cb";
                testAccessResult.style.color = "#721c24";
                testAccessResult.innerHTML = `⚠️ Network test error: ${err.message}`;
            } finally {
                testDriveAccessBtn.disabled = false;
                testDriveAccessBtn.textContent = "🔍 Test & Verify Folder Access";
            }
        });
    }

    // Drive Sync Trigger
    driveSyncForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const url = driveUrl.value.trim();
        if (!url) {
            showSyncResult("error", "Please paste a Google Drive folder link or folder ID.");
            return;
        }

        syncResultBanner.style.display = "none";
        syncProgressContainer.style.display = "block";
        startSyncBtn.disabled = true;
        startSyncBtn.textContent = "⏳ Starting Drive Indexing...";

        try {
            const res = await fetch("/api/sync/drive", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ folder_url: url })
            });

            if (res.status === 401) {
                startSyncBtn.disabled = false;
                startSyncBtn.textContent = "🔄 Index Faces from Google Drive";
                showSyncResult("error", "Session expired. Redirecting to login...");
                setTimeout(() => { window.location.href = "/login"; }, 1500);
                return;
            }

            const data = await res.json().catch(() => null);

            if (res.ok && data && data.success) {
                pollSyncStatus();
            } else {
                startSyncBtn.disabled = false;
                startSyncBtn.textContent = "🔄 Index Faces from Google Drive";
                const errorMsg = (data && (data.error || data.detail)) || `Server returned HTTP ${res.status}`;
                showSyncResult("error", errorMsg);
            }
        } catch (err) {
            startSyncBtn.disabled = false;
            startSyncBtn.textContent = "🔄 Index Faces from Google Drive";
            if (err.message && (err.message.includes("Failed to fetch") || err.name === "TypeError")) {
                showSyncResult("error", `Cannot connect to server at ${window.location.origin}. Please make sure the Python server is running (e.g. double-click run_app.bat or start_production.bat).`);
            } else {
                showSyncResult("error", "Error starting sync: " + err.message);
            }
        }
    });

    // Poll Sync Status
    function pollSyncStatus() {
        if (syncPollTimer) clearInterval(syncPollTimer);

        syncPollTimer = setInterval(async () => {
            try {
                const res = await fetch("/api/sync/status");
                const data = await res.json();

                syncActionText.textContent = data.current_action || "Syncing...";
                syncPercentText.textContent = `${data.progress_percent}%`;
                syncProgressBar.style.width = `${data.progress_percent}%`;

                syncFound.textContent = data.total_found;
                syncAdded.textContent = data.photos_added;
                syncSkipped.textContent = data.photos_skipped;
                syncFaces.textContent = data.faces_detected;

                if (!data.running) {
                    clearInterval(syncPollTimer);
                    startSyncBtn.disabled = false;
                    startSyncBtn.textContent = "🔄 Sync Photos from Google Drive";

                    if (data.error) {
                        showSyncResult("error", data.error);
                    } else if (data.total_found === 0) {
                        showSyncResult("warning", data.current_action || "No photos found. Please make sure the folder is shared with: suv-photography@suv-photography-514705.iam.gserviceaccount.com");
                    } else {
                        showSyncResult("success", `Sync complete! ${data.photos_added} new photos added (${data.faces_detected} faces detected), ${data.photos_skipped} existing photos skipped.`);
                    }
                    loadStats();
                    loadPhotos();
                }
            } catch (err) {
                console.warn("Poll status error:", err);
            }
        }, 1000);
    }

    function showSyncResult(type, msg) {
        syncResultBanner.className = `alert alert-${type}`;
        syncResultBanner.textContent = msg;
        syncResultBanner.style.display = "block";
    }

    // Direct Upload Dropzone
    dropzone.addEventListener("click", () => directUploadInput.click());

    ["dragenter", "dragover"].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        }, false);
    });

    ["dragleave", "drop"].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        }, false);
    });

    dropzone.addEventListener("drop", (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    });

    directUploadInput.addEventListener("change", (e) => {
        handleFiles(e.target.files);
    });

    async function handleFiles(files) {
        if (!files.length) return;

        uploadStatus.className = "alert alert-info";
        uploadStatus.textContent = `Uploading & processing ${files.length} photo(s)...`;
        uploadStatus.style.display = "block";

        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
            formData.append("files", files[i]);
        }

        try {
            const res = await fetch("/api/photos/upload", {
                method: "POST",
                body: formData
            });
            const data = await res.json();
            if (data.success) {
                uploadStatus.className = "alert alert-success";
                uploadStatus.textContent = `Successfully processed: ${data.added} photo(s) added, ${data.faces} face(s) indexed.`;
                loadStats();
                loadPhotos();
            } else {
                uploadStatus.className = "alert alert-error";
                uploadStatus.textContent = "Upload failed: " + (data.error || "Unknown error");
            }
        } catch (err) {
            uploadStatus.className = "alert alert-error";
            uploadStatus.textContent = "Network error uploading photos: " + err.message;
        }
    }

    // Settings & Slider
    thresholdSlider.addEventListener("input", (e) => {
        thresholdVal.textContent = e.target.value;
    });

    // Cover Photo Upload
    if (coverPhotoFileInput) {
        coverPhotoFileInput.addEventListener("change", async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            coverPhotoStatus.textContent = "Uploading cover photo...";
            coverPhotoStatus.style.color = "#722234";

            const formData = new FormData();
            formData.append("file", file);

            try {
                const res = await fetch("/api/settings/cover-photo", {
                    method: "POST",
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    coverPhotoPreview.src = data.cover_photo_path;
                    const miniMockupImg = document.getElementById("miniMockupImg");
                    if (miniMockupImg) miniMockupImg.src = data.cover_photo_path;
                    coverPhotoStatus.textContent = "✅ Cover photo updated successfully!";
                    coverPhotoStatus.style.color = "#15803d";
                } else {
                    coverPhotoStatus.textContent = "❌ " + (data.detail || "Upload failed");
                    coverPhotoStatus.style.color = "#b91c1c";
                }
            } catch (err) {
                coverPhotoStatus.textContent = "❌ Network error uploading photo: " + err.message;
                coverPhotoStatus.style.color = "#b91c1c";
            }
        });
    }

    // Reset Cover Photo to Default
    if (resetCoverPhotoBtn) {
        resetCoverPhotoBtn.addEventListener("click", async () => {
            if (!confirm("Reset cover photo to default wedding image?")) return;
            try {
                const res = await fetch("/api/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        cover_photo_path: "/static/img/default_wedding_cover_clean.jpg"
                    })
                });
                const data = await res.json();
                if (data.success) {
                    coverPhotoPreview.src = "/static/img/default_wedding_cover_clean.jpg";
                    const miniMockupImg = document.getElementById("miniMockupImg");
                    if (miniMockupImg) miniMockupImg.src = "/static/img/default_wedding_cover_clean.jpg";
                    coverPhotoStatus.textContent = "✅ Reset to default wedding photo";
                    coverPhotoStatus.style.color = "#15803d";
                }
            } catch (err) {
                alert("Failed to reset cover photo: " + err.message);
            }
        });
    }

    // Live update mini mockup
    const miniMockupNames = document.getElementById("miniMockupNames");
    const miniMockupDate = document.getElementById("miniMockupDate");
    const miniMockupLoc = document.getElementById("miniMockupLoc");

    if (coupleTitleInput && miniMockupNames) {
        coupleTitleInput.addEventListener("input", (e) => {
            miniMockupNames.textContent = e.target.value.trim() || "Bride & Groom";
        });
    }
    if (eventDateInput && miniMockupDate) {
        eventDateInput.addEventListener("input", (e) => {
            miniMockupDate.textContent = e.target.value.trim() || "17 NOV 2026";
        });
    }
    if (eventLocationInput && miniMockupLoc) {
        eventLocationInput.addEventListener("input", (e) => {
            miniMockupLoc.textContent = e.target.value.trim() || "CHENNAI";
        });
    }

    // Print QR Standee / Poster
    const printQrBtn = document.getElementById("printQrBtn");
    if (printQrBtn && qrImage) {
        printQrBtn.addEventListener("click", () => {
            const qrSrc = qrImage.src;
            const printWin = window.open("", "_blank");
            if (!printWin) {
                alert("Please allow popups to print the QR standee.");
                return;
            }
            printWin.document.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Print QR Standee - SUV Photography</title>
                    <style>
                        body {
                            font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
                            text-align: center;
                            padding: 40px 20px;
                            color: #4a1521;
                            background: #ffffff;
                        }
                        .standee-card {
                            max-width: 380px;
                            margin: 0 auto;
                            border: 2px solid #722234;
                            border-radius: 24px;
                            padding: 32px 24px;
                            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
                        }
                        .brand-title {
                            font-size: 24px;
                            font-weight: 800;
                            letter-spacing: 1px;
                            margin-bottom: 2px;
                            text-transform: uppercase;
                        }
                        .brand-sub {
                            font-size: 13px;
                            font-weight: 700;
                            color: #722234;
                            letter-spacing: 3px;
                            margin-bottom: 20px;
                        }
                        .qr-box {
                            background: #ffffff;
                            padding: 16px;
                            border-radius: 16px;
                            display: inline-block;
                            border: 1px solid #ddd;
                        }
                        .qr-box img {
                            width: 240px;
                            height: 240px;
                            display: block;
                        }
                        .cta-text {
                            font-size: 18px;
                            font-weight: 800;
                            margin: 18px 0 6px;
                        }
                        .inst-text {
                            font-size: 14px;
                            color: #666;
                            margin-bottom: 24px;
                        }
                        .no-print-btn {
                            background: #722234;
                            color: #ffffff;
                            border: none;
                            padding: 12px 28px;
                            font-size: 15px;
                            font-weight: 700;
                            border-radius: 9999px;
                            cursor: pointer;
                        }
                        @media print {
                            .no-print-btn { display: none; }
                            body { padding: 0; }
                            .standee-card { border: none; box-shadow: none; }
                        }
                    </style>
                </head>
                <body>
                    <div class="standee-card">
                        <div class="brand-title">SUV Photography</div>
                        <div class="brand-sub">INSTANT AI PHOTO FINDER</div>
                        <div class="qr-box">
                            <img src="${qrSrc}" alt="QR Code">
                        </div>
                        <div class="cta-text">Scan with Mobile Camera</div>
                        <div class="inst-text">Take a selfie to find all your event photos instantly!</div>
                        <button class="no-print-btn" onclick="window.print()">🖨️ Print Standee Poster</button>
                    </div>
                </body>
                </html>
            `);
            printWin.document.close();
        });
    }

    settingsForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const coupleTitle = coupleTitleInput ? coupleTitleInput.value.trim() : "";
        const eventDate = eventDateInput ? eventDateInput.value.trim() : "";
        const eventLocation = eventLocationInput ? eventLocationInput.value.trim() : "";
        const thresh = parseFloat(thresholdSlider.value);

        const payload = {
            couple_title: coupleTitle,
            event_date: eventDate,
            event_location: eventLocation,
            match_threshold: thresh
        };
        if (eventNameInput && eventNameInput.value.trim()) {
            payload.event_name = eventNameInput.value.trim();
        }

        try {
            const res = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.success) {
                if (navEventName && payload.event_name) navEventName.textContent = payload.event_name;
                if (statThreshold) statThreshold.textContent = thresh;
                alert("✅ Event & Cover details saved successfully!");
            }
        } catch (err) {
            alert("Failed to save settings: " + err.message);
        }
    });

    // Load Stats & Event Settings dynamically (supports static Vercel hosting)
    async function loadStats() {
        try {
            const res = await fetch("/api/dashboard/stats");
            if (res.status === 401 || res.status === 403) {
                window.location.href = "/login";
                return;
            }
            const data = await res.json();
            if (data.success) {
                if (statTotalPhotos) statTotalPhotos.textContent = data.stats.total_photos;
                if (statTotalFaces) statTotalFaces.textContent = data.stats.total_faces;
                if (statMultiFaces) statMultiFaces.textContent = data.stats.multi_face_photos;
                if (statThreshold) statThreshold.textContent = data.settings.match_threshold;
                if (thresholdSlider) thresholdSlider.value = data.settings.match_threshold;
                if (thresholdVal) thresholdVal.textContent = data.settings.match_threshold;
                if (eventNameInput && (!eventNameInput.value || eventNameInput.value.includes("{{"))) {
                    eventNameInput.value = data.settings.event_name || "";
                }
                if (coupleTitleInput && (!coupleTitleInput.value || coupleTitleInput.value.includes("{{"))) {
                    coupleTitleInput.value = data.settings.couple_title || "Bride & Groom";
                }
                if (eventDateInput && (!eventDateInput.value || eventDateInput.value.includes("{{"))) {
                    eventDateInput.value = data.settings.event_date || "17 NOV 2026";
                }
                if (eventLocationInput && (!eventLocationInput.value || eventLocationInput.value.includes("{{"))) {
                    eventLocationInput.value = data.settings.event_location || "CHENNAI";
                }
                if (driveUrl && (!driveUrl.value || driveUrl.value.includes("{{"))) {
                    driveUrl.value = data.settings.drive_folder_url || "";
                }
                if (navEventName) {
                    navEventName.innerHTML = `Host Dashboard &bull; ${escapeHtml(data.settings.event_name || 'SUV Event')}`;
                }
            }
        } catch (err) {
            console.error("Failed to load stats:", err);
        }
    }

    async function loadQrData() {
        try {
            const res = await fetch("/api/dashboard/qr");
            const data = await res.json();
            if (data.success) {
                if (qrImage && data.qr_image) qrImage.src = data.qr_image;
                if (guestUrlInput && data.guest_url) guestUrlInput.value = data.guest_url;
            }
        } catch (err) {}
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

    // Photo Library List (safely guarded if component is removed)
    async function loadPhotos() {
        if (!photosLoading || !photosList) return;
        photosLoading.style.display = "block";
        photosList.style.display = "none";
        if (photosEmpty) photosEmpty.style.display = "none";

        try {
            const res = await fetch("/api/photos/list?limit=100");
            const data = await res.json();
            if (photosLoading) photosLoading.style.display = "none";

            if (data.success && data.photos.length > 0) {
                photosList.innerHTML = "";
                data.photos.forEach(photo => {
                    const card = document.createElement("div");
                    card.className = "dash-photo-card";

                    const faceBadgeClass = photo.face_count > 0 ? "has-faces" : "no-faces";
                    const faceBadgeText = photo.face_count > 0 ? `${photo.face_count} face${photo.face_count === 1 ? '' : 's'}` : "0 faces";
                    const safeName = escapeHtml(photo.filename);

                    card.innerHTML = `
                        <img src="${photo.thumbnail_url}" alt="${safeName}" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='/api/thumbnails/${photo.id}';">
                        <div class="dash-photo-info">
                            <span class="dash-face-pill ${faceBadgeClass}">${faceBadgeText}</span>
                            <button type="button" class="dash-delete-btn" data-id="${photo.id}" title="Delete Photo">🗑️</button>
                        </div>
                    `;

                    card.querySelector(".dash-delete-btn").addEventListener("click", async () => {
                        if (confirm(`Delete ${photo.filename}?`)) {
                            await deletePhoto(photo.id);
                        }
                    });

                    photosList.appendChild(card);
                });
                photosList.style.display = "grid";
            } else if (photosEmpty) {
                photosEmpty.style.display = "block";
            }
        } catch (err) {
            if (photosLoading) photosLoading.style.display = "none";
            if (photosEmpty) {
                photosEmpty.textContent = "Error loading photos: " + err.message;
                photosEmpty.style.display = "block";
            }
        }
    }

    async function deletePhoto(photoId) {
        try {
            const res = await fetch(`/api/photos/${photoId}`, { method: "DELETE" });
            const data = await res.json();
            if (data.success) {
                loadPhotos();
                loadStats();
            }
        } catch (err) {
            alert("Failed to delete photo: " + err.message);
        }
    }

    const clearOldPhotosBtn = document.getElementById("clearOldPhotosBtn");
    if (clearOldPhotosBtn) {
        clearOldPhotosBtn.addEventListener("click", async () => {
            if (!confirm("This will remove indexed photos from previous Google Drive links so guest face search ONLY matches the active event. Continue?")) {
                return;
            }
            try {
                const res = await fetch("/api/photos/clear-previous", {
                    method: "POST",
                    credentials: "include"
                });
                const data = await res.json();
                if (data.success) {
                    alert("Cleaned up old event photos successfully!");
                    loadStats();
                    loadPhotos();
                }
            } catch (err) {
                alert("Error clearing photos: " + err.message);
            }
        });
    }

    if (refreshPhotosBtn) {
        refreshPhotosBtn.addEventListener("click", loadPhotos);
    }

    // Initial load (dynamic hydration)
    loadStats();
    loadQrData();
    if (photosList) {
        loadPhotos();
    }
});
