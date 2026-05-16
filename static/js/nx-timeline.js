/**
 * nx-timeline.js — Nexora Execution Replay & Timeline Component
 * ══════════════════════════════════════════════════════════════════════
 * Renders an interactive, scrubbable timeline of execution events.
 * Connects directly to the backend ExecutionReplayEngine payload.
 */
(function() {
    'use strict';

    class NxTimeline {
        constructor(containerId) {
            this.container = document.getElementById(containerId);
            this.executionId = null;
            this.timelineData = [];
            this.summaryData = {};
        }

        async loadExecution(executionId) {
            this.executionId = executionId;
            try {
                const res = await fetch(`/api/v2/admin/replay/${executionId}`);
                const data = await res.json();
                
                if (data.ok) {
                    this.timelineData = data.data.timeline;
                    this.summaryData = data.data.summary;
                    this.render();
                } else {
                    this.renderError(data.error.message);
                }
            } catch (err) {
                this.renderError("Failed to fetch execution timeline.");
            }
        }

        render() {
            if (!this.container) return;
            
            let html = `<div class="nx-timeline-header">
                <h3>Execution Replay: ${this.executionId}</h3>
                <div class="nx-timeline-stats">
                    <span class="nx-pill ${this.summaryData.has_errors ? 'error' : 'success'}">${this.summaryData.final_status}</span>
                    <span>Tools: ${this.summaryData.total_tools_called}</span>
                    <span>Files: ${this.summaryData.total_files_modified}</span>
                    <span>Duration: ${this.summaryData.duration_ms}ms</span>
                </div>
            </div>`;

            html += `<div class="nx-timeline-track">`;
            
            if (this.timelineData.length === 0) {
                html += `<div class="nx-timeline-empty">No events recorded.</div>`;
            }

            this.timelineData.forEach(evt => {
                let icon = '⚡';
                let cls = 'info';
                let title = evt.event || evt.type;
                let desc = '';

                if (evt.type === 'tool') {
                    icon = '🔧';
                    cls = 'tool';
                    desc = `Tool: ${evt.tool}`;
                } else if (evt.type === 'filesystem') {
                    icon = '📄';
                    cls = 'file';
                    desc = `File: ${evt.file}`;
                } else if (evt.event === 'task.failed') {
                    icon = '❌';
                    cls = 'error';
                    desc = evt.details.error || 'Unknown error';
                } else if (evt.event === 'task.completed') {
                    icon = '✅';
                    cls = 'success';
                }

                const timeStr = new Date(evt.timestamp * 1000).toISOString().substr(11, 8);

                html += `
                    <div class="nx-timeline-node ${cls}">
                        <div class="nx-timeline-time">${timeStr}</div>
                        <div class="nx-timeline-marker">${icon}</div>
                        <div class="nx-timeline-content">
                            <strong>${title}</strong>
                            ${desc ? `<span>${desc}</span>` : ''}
                        </div>
                    </div>
                `;
            });

            html += `</div>`;
            
            // Render scrubber controls
            html += `
            <div class="nx-timeline-controls">
                <button class="nds-btn secondary" onclick="window.NxHitl.seekStart('${this.executionId}')">⏮ Seek Start</button>
                <button class="nds-btn secondary" onclick="window.NxHitl.seekEnd('${this.executionId}')">⏭ Seek End</button>
            </div>`;

            this.container.innerHTML = html;
        }

        renderError(msg) {
            if (this.container) {
                this.container.innerHTML = `<div class="nx-alert error">Replay Error: ${msg}</div>`;
            }
        }
    }

    // Expose globally
    window.NxTimeline = NxTimeline;
})();
