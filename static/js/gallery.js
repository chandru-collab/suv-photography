/**
 * Google Drive Event Gallery - Frontend Controller
 * Direct Google Drive API Integration - Zero Local File Duplication
 */

(function () {
    'use strict';

    // State
    let currentFolderId = '';
    let rootFolderId = '';
    let currentData = {
        folder: {},
        breadcrumbs: [],
        subfolders: [],
        photos: [],
        videos: []
    };
    let activeFilterType = 'all';
    let searchQuery = '';
    let nextPageToken = null;
    let folderHistory = [];
    let currentParentFolder = null;

    // DOM Elements
    const elements = {
        config: document.getElementById('galleryConfig'),
        breadcrumbs: document.getElementById('breadcrumbs'),
        currentFolderName: document.getElementById('currentFolderName'),
        currentFolderDesc: document.getElementById('currentFolderDesc'),
        openInDriveBtn: document.getElementById('openInDriveBtn'),
        syncActiveFolderBtn: document.getElementById('syncActiveFolderBtn'),
        subfoldersSection: document.getElementById('subfoldersSection'),
        subfoldersGrid: document.getElementById('subfoldersGrid'),
        mediaSection: document.getElementById('mediaSection'),
        mediaGrid: document.getElementById('mediaGrid'),
        galleryLoading: document.getElementById('galleryLoading'),
        galleryEmpty: document.getElementById('galleryEmpty'),
        loadMoreContainer: document.getElementById('loadMoreContainer'),
        loadMoreBtn: document.getElementById('loadMoreBtn'),
        gallerySearch: document.getElementById('gallerySearch'),
        refreshGalleryBtn: document.getElementById('refreshGalleryBtn'),
        filterChips: document.querySelectorAll('.filter-chip'),
        countAll: document.getElementById('countAll'),
        countPhotos: document.getElementById('countPhotos'),
        countVideos: document.getElementById('countVideos'),
        // Return Arrow Navigation
        headerBackBtn: document.getElementById('headerBackBtn'),
        // Lightbox
        lightboxModal: document.getElementById('lightboxModal'),
        lightboxImage: document.getElementById('lightboxImage'),
        lightboxTitle: document.getElementById('lightboxTitle'),
        lightboxDims: document.getElementById('lightboxDims'),
        lightboxDownloadBtn: document.getElementById('lightboxDownloadBtn'),
        lightboxDriveBtn: document.getElementById('lightboxDriveBtn'),
        closeLightboxBtn: document.getElementById('closeLightboxBtn'),
        // Video Modal
        videoModal: document.getElementById('videoModal'),
        videoPlayer: document.getElementById('videoPlayer'),
        videoSource: document.getElementById('videoSource'),
        videoTitle: document.getElementById('videoTitle'),
        videoDuration: document.getElementById('videoDuration'),
        videoDownloadBtn: document.getElementById('videoDownloadBtn'),
        videoDriveBtn: document.getElementById('videoDriveBtn'),
        closeVideoBtn: document.getElementById('closeVideoBtn')
    };

    function init() {
        if (elements.config) {
            const rawInit = elements.config.dataset.initialFolderId || '';
            const rawRoot = elements.config.dataset.rootFolderId || '';
            currentFolderId = rawInit.includes('{{') ? '' : rawInit;
            rootFolderId = rawRoot.includes('{{') ? '' : rawRoot;
        }

        // Event listeners
        elements.gallerySearch.addEventListener('input', (e) => {
            searchQuery = e.target.value.toLowerCase().trim();
            renderMediaGrid();
        });

        elements.refreshGalleryBtn.addEventListener('click', () => {
            loadFolder(currentFolderId, false);
        });

        elements.filterChips.forEach(chip => {
            chip.addEventListener('click', () => {
                elements.filterChips.forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                activeFilterType = chip.dataset.type;
                renderMediaGrid();
            });
        });

        elements.syncActiveFolderBtn.addEventListener('click', triggerFaceSync);

        // Return Arrow Navigation
        if (elements.headerBackBtn) {
            elements.headerBackBtn.addEventListener('click', navigateBack);
        }

        // Browser Back / Forward Button Support
        window.addEventListener('popstate', (e) => {
            if (e.state && e.state.folderId) {
                loadFolder(e.state.folderId, false, false);
            } else {
                const match = window.location.pathname.match(/\/gallery\/([a-zA-Z0-9_\-]+)/);
                const target = (match && match[1]) ? match[1] : (rootFolderId || 'root');
                loadFolder(target, false, false);
            }
        });

        // Lightbox Close
        elements.closeLightboxBtn.addEventListener('click', closeLightbox);
        elements.lightboxModal.addEventListener('click', (e) => {
            if (e.target === elements.lightboxModal) closeLightbox();
        });

        // Video Modal Close
        elements.closeVideoBtn.addEventListener('click', closeVideoModal);
        elements.videoModal.addEventListener('click', (e) => {
            if (e.target === elements.videoModal) closeVideoModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeLightbox();
                closeVideoModal();
            }
        });

        if (elements.loadMoreBtn) {
            elements.loadMoreBtn.addEventListener('click', () => {
                if (nextPageToken) {
                    loadFolder(currentFolderId, true);
                }
            });
        }

        // Initial Load
        loadFolder(currentFolderId);
    }

    function navigateBack() {
        if (currentParentFolder && currentParentFolder.id) {
            const targetId = currentParentFolder.id;
            if (folderHistory.length > 0 && folderHistory[folderHistory.length - 1] === targetId) {
                folderHistory.pop();
            }
            loadFolder(targetId, false, false);
        } else if (folderHistory.length > 0) {
            const prevId = folderHistory.pop();
            loadFolder(prevId, false, false);
        } else if (rootFolderId && rootFolderId !== currentFolderId) {
            loadFolder(rootFolderId, false, false);
        }
    }

    async function loadFolder(folderId, append = false, recordHistory = true) {
        if (!append) {
            if (recordHistory && currentFolderId && currentFolderId !== folderId) {
                folderHistory.push(currentFolderId);
            }
            elements.galleryLoading.style.display = 'block';
            elements.subfoldersSection.style.display = 'none';
            elements.mediaSection.style.display = 'none';
            elements.galleryEmpty.style.display = 'none';
        }

        try {
            const url = new URL(`/api/drive/gallery/${folderId || 'root'}`, window.location.origin);
            if (append && nextPageToken) {
                url.searchParams.set('pageToken', nextPageToken);
            }

            const response = await fetch(url.toString());
            const data = await response.json();

            if (!data.success) {
                elements.galleryLoading.style.display = 'none';
                elements.galleryEmpty.style.display = 'block';
                elements.galleryEmpty.querySelector('h3').textContent = 'Drive Access Error';
                elements.galleryEmpty.querySelector('p').textContent = data.error || 'Failed to access Google Drive folder.';
                return;
            }

            currentFolderId = data.folder.id;
            nextPageToken = data.next_page_token || null;

            if (append) {
                currentData.photos = currentData.photos.concat(data.photos || []);
                currentData.videos = currentData.videos.concat(data.videos || []);
            } else {
                currentData = data;
            }

            updateFolderHeader();
            renderBreadcrumbs();
            renderSubfolders();
            renderMediaGrid();

            elements.galleryLoading.style.display = 'none';
            history.pushState({ folderId: currentFolderId }, '', `/gallery/${currentFolderId}`);
        } catch (err) {
            console.error('Error loading gallery from Drive API:', err);
            elements.galleryLoading.style.display = 'none';
            elements.galleryEmpty.style.display = 'block';
            elements.galleryEmpty.querySelector('p').textContent = 'Network or API error communicating with Google Drive.';
        }
    }

    function updateFolderHeader() {
        const folder = currentData.folder || {};
        elements.currentFolderName.textContent = folder.name || 'Google Drive Gallery';
        elements.openInDriveBtn.href = folder.webViewLink || `https://drive.google.com/drive/folders/${currentFolderId}`;

        const totalPhotos = (currentData.photos || []).length;
        const totalVideos = (currentData.videos || []).length;
        elements.currentFolderDesc.textContent = `${totalPhotos} photo(s), ${totalVideos} video(s) retrieved directly from Google Drive API.`;

        elements.countAll.textContent = totalPhotos + totalVideos;
        elements.countPhotos.textContent = totalPhotos;
        elements.countVideos.textContent = totalVideos;

        // Check if inside a subfolder and identify parent folder
        const breadcrumbs = currentData.breadcrumbs || [];
        const isSubfolder = breadcrumbs.length > 1 || (currentData.parent_id && currentData.parent_id !== 'root') || (rootFolderId && rootFolderId !== currentFolderId) || folderHistory.length > 0;

        if (isSubfolder) {
            let parentId = null;
            let parentName = 'Parent Folder';

            if (breadcrumbs.length >= 2) {
                const parentCrumb = breadcrumbs[breadcrumbs.length - 2];
                parentId = parentCrumb.id;
                parentName = parentCrumb.name;
            } else if (folderHistory.length > 0) {
                parentId = folderHistory[folderHistory.length - 1];
            } else if (currentData.parent_id) {
                parentId = currentData.parent_id;
            } else if (rootFolderId && rootFolderId !== currentFolderId) {
                parentId = rootFolderId;
            }

            currentParentFolder = { id: parentId, name: parentName };

            // Show single return arrow button beside folder title
            if (elements.headerBackBtn) {
                elements.headerBackBtn.style.display = 'inline-flex';
                elements.headerBackBtn.title = `Return to ${parentName} (←)`;
            }
        } else {
            currentParentFolder = null;
            if (elements.headerBackBtn) elements.headerBackBtn.style.display = 'none';
        }
    }

    function renderBreadcrumbs() {
        const breadcrumbs = currentData.breadcrumbs || [];
        elements.breadcrumbs.innerHTML = '';

        breadcrumbs.forEach((crumb, idx) => {
            const isLast = idx === breadcrumbs.length - 1;
            const item = document.createElement(isLast ? 'span' : 'a');
            item.className = `breadcrumb-item ${isLast ? 'active' : ''}`;
            item.textContent = crumb.name;

            if (!isLast) {
                item.href = `#`;
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    loadFolder(crumb.id);
                });
            }

            elements.breadcrumbs.appendChild(item);

            if (!isLast) {
                const sep = document.createElement('span');
                sep.className = 'breadcrumb-sep';
                sep.textContent = '/';
                elements.breadcrumbs.appendChild(sep);
            }
        });
    }

    function renderSubfolders() {
        const subfolders = currentData.subfolders || [];
        if (subfolders.length === 0) {
            elements.subfoldersSection.style.display = 'none';
            return;
        }

        elements.subfoldersGrid.innerHTML = '';
        subfolders.forEach(sub => {
            const card = document.createElement('div');
            card.className = 'folder-card';
            card.innerHTML = `
                <div class="folder-card-icon">📁</div>
                <div style="overflow: hidden;">
                    <div class="folder-card-title" title="${escapeHtml(sub.name)}">${escapeHtml(sub.name)}</div>
                    <div class="folder-card-subtitle">Click to view collection</div>
                </div>
            `;
            card.addEventListener('click', () => {
                loadFolder(sub.id);
            });
            elements.subfoldersGrid.appendChild(card);
        });

        elements.subfoldersSection.style.display = 'block';
    }

    function renderMediaGrid() {
        const photos = currentData.photos || [];
        const videos = currentData.videos || [];

        let items = [];
        if (activeFilterType === 'all') {
            items = [...photos.map(p => ({ ...p, mediaType: 'photo' })), ...videos.map(v => ({ ...v, mediaType: 'video' }))];
        } else if (activeFilterType === 'photos') {
            items = photos.map(p => ({ ...p, mediaType: 'photo' }));
        } else if (activeFilterType === 'videos') {
            items = videos.map(v => ({ ...v, mediaType: 'video' }));
        }

        // Apply search query filter
        if (searchQuery) {
            items = items.filter(item => (item.name || '').toLowerCase().includes(searchQuery));
        }

        if (items.length === 0) {
            elements.mediaSection.style.display = 'none';
            if ((currentData.subfolders || []).length === 0) {
                elements.galleryEmpty.style.display = 'block';
            }
            return;
        }

        elements.galleryEmpty.style.display = 'none';
        elements.mediaGrid.innerHTML = '';

        items.forEach(item => {
            const card = document.createElement('div');
            card.className = 'media-card';

            const isVideo = item.mediaType === 'video';
            const thumbUrl = item.cdn_thumb || item.thumbnail_url;
            const sizeStr = formatBytes(item.size);

            card.innerHTML = `
                <div class="media-thumb-box">
                    <img class="media-thumb-img" src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(item.name)}" loading="lazy" onerror="if(!this.dataset.retried){this.dataset.retried=1;this.src='/api/drive/media/${item.id}';}">
                    <span class="media-badge ${isVideo ? 'badge-video' : ''}">
                        ${isVideo ? '🎥 Video' : '📷 Photo'}
                    </span>
                    ${isVideo ? '<div class="video-play-overlay">▶</div>' : ''}
                </div>
                <div class="media-meta-bar">
                    <div style="overflow: hidden;">
                        <div class="media-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                        <div class="media-size">${sizeStr}</div>
                    </div>
                    <div class="media-actions">
                        <a href="/api/drive/media/${item.id}?download=true" class="action-icon-btn" title="Download Original" download>
                            ⬇️
                        </a>
                        <a href="${item.webViewLink || '#'}" target="_blank" class="action-icon-btn" title="Open in Google Drive">
                            ↗️
                        </a>
                    </div>
                </div>
            `;

            // Click on thumbnail opens lightbox / video modal
            const thumbBox = card.querySelector('.media-thumb-box');
            thumbBox.addEventListener('click', () => {
                if (isVideo) {
                    openVideoModal(item);
                } else {
                    openLightbox(item);
                }
            });

            elements.mediaGrid.appendChild(card);
        });

        elements.mediaSection.style.display = 'block';

        if (elements.loadMoreContainer) {
            elements.loadMoreContainer.style.display = nextPageToken ? 'block' : 'none';
        }
    }

    function openLightbox(photo) {
        elements.lightboxImage.src = photo.cdn_preview || photo.preview_url || photo.thumbnail_url;
        elements.lightboxTitle.textContent = photo.name;
        elements.lightboxDims.textContent = (photo.width && photo.height) ? `${photo.width} × ${photo.height} &bull; ${formatBytes(photo.size)}` : formatBytes(photo.size);
        elements.lightboxDownloadBtn.href = `/api/drive/media/${photo.id}?download=true`;
        elements.lightboxDriveBtn.href = photo.webViewLink || `https://drive.google.com/file/d/${photo.id}/view`;
        elements.lightboxModal.style.display = 'flex';
    }

    function closeLightbox() {
        elements.lightboxModal.style.display = 'none';
        elements.lightboxImage.src = '';
    }

    function openVideoModal(video) {
        elements.videoTitle.textContent = video.name;
        elements.videoDuration.textContent = formatBytes(video.size);
        elements.videoDownloadBtn.href = `/api/drive/media/${video.id}?download=true`;
        elements.videoDriveBtn.href = video.webViewLink || `https://drive.google.com/file/d/${video.id}/view`;

        const streamUrl = `/api/drive/media/${video.id}`;
        // Set both source and videoPlayer for full cross-browser HTML5 video compatibility
        elements.videoSource.src = streamUrl;
        elements.videoPlayer.src = streamUrl;
        elements.videoPlayer.load();
        elements.videoModal.style.display = 'flex';
        elements.videoPlayer.play().catch(() => {});
    }

    function closeVideoModal() {
        elements.videoPlayer.pause();
        elements.videoPlayer.removeAttribute('src');
        elements.videoSource.removeAttribute('src');
        elements.videoPlayer.load();
        elements.videoModal.style.display = 'none';
    }

    async function triggerFaceSync() {
        if (!currentFolderId) return;
        const btn = elements.syncActiveFolderBtn;
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '🔄 Starting Indexing...';

        try {
            const resp = await fetch('/api/sync/drive', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_url: currentFolderId })
            });

            if (resp.status === 401) {
                alert("Host authentication required. Please log in to the dashboard first.");
                window.location.href = "/login";
                return;
            }

            const res = await resp.json().catch(() => null);
            if (resp.ok && res && res.success) {
                alert(`Face recognition indexing started for this folder in the background!\nPhotos are processed directly in-memory from Google Drive.`);
            } else {
                const msg = (res && (res.error || res.detail)) || `Server returned HTTP ${resp.status}`;
                alert(`Could not start face indexing: ${msg}`);
            }
        } catch (e) {
            alert(`Cannot reach server at ${window.location.origin}. Please ensure the Python server is running (e.g. run_app.bat).`);
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    function formatBytes(bytes) {
        if (!bytes || bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    function extractFolderId(text) {
        if (!text) return null;
        text = text.trim();
        const m1 = text.match(/folders\/([a-zA-Z0-9_\-]+)/);
        if (m1) return m1[1];
        const m2 = text.match(/[?&]id=([a-zA-Z0-9_\-]+)/);
        if (m2) return m2[1];
        if (/^[a-zA-Z0-9_\-]{20,}$/.test(text)) return text;
        return null;
    }

    function escapeHtml(text) {
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    document.addEventListener('DOMContentLoaded', init);
})();
