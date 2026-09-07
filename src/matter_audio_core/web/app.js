"use strict";
const $ = id => document.getElementById(id);
const token = location.hash.slice(1);
let state, previewId, variantId, context, playing = [], playbackEpoch = 0, looped = false, saving = false;
const buffers = new Map();
const pendingKey = "matter-pending:" + token;
const uid = prefix => prefix + "-" + crypto.randomUUID();
function notice(message, error = false) { $("notice").textContent = message; $("notice").className = error ? "error" : ""; }
async function api(path, body) {
  const response = await fetch(path, {method: body ? "POST" : "GET", headers: {"X-Matter-Token": token, ...(body ? {"Content-Type": "application/json"} : {})}, body: body ? JSON.stringify(body) : undefined});
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error?.message || "请求失败"); error.code = value.error?.code; error.status = response.status; throw error; }
  return value;
}
async function recoverPending() {
  const encoded = sessionStorage.getItem(pendingKey);
  if (!encoded) return;
  const pending = JSON.parse(encoded);
  try {
    const result = await api(pending.path, pending.body);
    sessionStorage.removeItem(pendingKey);
    notice("上次保存已确认；会话已恢复。");
    return result;
  } catch (error) {
    if (error.status >= 400 && error.status < 500 && error.code !== "io_error") sessionStorage.removeItem(pendingKey);
    throw error;
  }
}
async function mutation(path, body) {
  if (saving) throw new Error("正在保存，请稍候。");
  if (sessionStorage.getItem(pendingKey)) throw new Error("上次保存尚未确认，请点击刷新会话恢复。");
  saving = true;
  try { sessionStorage.setItem(pendingKey, JSON.stringify({path, body})); return await recoverPending(); }
  finally { saving = false; }
}
const current = () => state?.candidates.find(item => item.asset_id === previewId);
function range() {
  const item = current(), start = Number($("start").value), end = Number($("end").value);
  if (!Number.isInteger(start) || !Number.isInteger(end) || !(0 <= start && start < end && end <= item.media.frame_count)) throw new Error("请选择音频内的有效整数帧范围。");
  return {start, end, rate: item.media.sample_rate_hz};
}
function element(tag, text, className) { const item = document.createElement(tag); item.textContent = text; if (className) item.className = className; return item; }
function draw() {
  if (!state || !current()) return;
  const canvas = $("wave"), rect = canvas.getBoundingClientRect(), scale = devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * scale)); canvas.height = Math.round(rect.height * scale);
  const pen = canvas.getContext("2d"), w = canvas.width, h = canvas.height, item = current(), frames = item.media.frame_count;
  pen.clearRect(0, 0, w, h);
  for (const region of item.protected_regions) { pen.fillStyle = "#dda45045"; pen.fillRect(region.start_frame / frames * w, 0, (region.end_frame - region.start_frame) / frames * w, h); }
  let selected; try { selected = range(); } catch (_) { selected = null; }
  if (selected) { pen.fillStyle = "#a0d9b913"; pen.fillRect(selected.start / frames * w, 0, (selected.end - selected.start) / frames * w, h); }
  pen.strokeStyle = "#a6d8ba"; pen.lineWidth = Math.max(1, scale); pen.beginPath();
  item.waveform.forEach((peak, i) => { const x = i / item.waveform.length * w; pen.moveTo(x, h / 2 - peak[1] * h * .46); pen.lineTo(x, h / 2 - peak[0] * h * .46); }); pen.stroke();
  if (selected) { pen.strokeStyle = "#e7eee3"; pen.lineWidth = scale; for (const frame of [selected.start, selected.end]) { const x = frame / frames * w; pen.beginPath(); pen.moveTo(x, 0); pen.lineTo(x, h); pen.stroke(); } }
}
function activate(id, preserveRange = false, remember = true) {
  stop(); previewId = id;
  if (remember && id !== state.audition.request.reference_asset_id) variantId = id;
  const item = current();
  $("preview-title").textContent = item.label; $("format").textContent = `${item.media.sample_rate_hz.toLocaleString()} Hz / ${item.media.channels === 2 ? "STEREO" : "MONO"}`;
  $("duration").textContent = `${item.media.duration_seconds.toFixed(3)} s`;
  if (!preserveRange) { $("start").value = 0; $("end").value = item.media.frame_count; }
  else { $("end").value = Math.min(Number($("end").value), item.media.frame_count); if (Number($("start").value) >= Number($("end").value)) $("start").value = 0; }
  $("end").max = item.media.frame_count; $("start").max = item.media.frame_count - 1;
  $("protection").textContent = item.protected_regions.length ? item.protected_regions.map(r => `${(r.start_frame / item.media.sample_rate_hz).toFixed(3)}–${(r.end_frame / item.media.sample_rate_hz).toFixed(3)} s`).join(" · ") : (item.selection_conflict ? "此版本不满足当前保护规则" : "未设置");
  const savedId = state.session.current.selected_asset?.asset_id;
  $("select").disabled = !!item.selection_conflict || savedId === id;
  $("selection-info").textContent = item.selection_conflict ? "可以比较此版本；当前保护规则下无法保存为新选择。" : savedId === id ? "预览与已保存版本一致。" : "预览尚未保存。导出仍使用已保存版本。";
  document.querySelectorAll("#candidates button").forEach(button => { const active = button.dataset.asset === id; button.classList.toggle("active", active); button.setAttribute("aria-current", String(active)); });
  $("reference").setAttribute("aria-pressed", String(id === state.audition.request.reference_asset_id));
  $("variant").setAttribute("aria-pressed", String(id === variantId));
  updateRange();
}
function updateRange() { stop(); try { const r = range(); $("range-seconds").textContent = `${(r.start / r.rate).toFixed(3)} → ${(r.end / r.rate).toFixed(3)} 秒 · 共 ${r.end - r.start} 帧`; } catch (error) { $("range-seconds").textContent = error.message; } draw(); }
async function refresh() {
  state = await api("/api/state");
  document.querySelectorAll("button, input, textarea").forEach(item => { item.disabled = false; });
  $("title").textContent = state.audition.request.name;
  $("summary").textContent = `${state.session.session.name} · 修订 ${state.session.session.head_revision} · ${state.candidates.length} 个可比较版本`;
  $("count").textContent = String(state.candidates.length);
  const savedId = state.session.current.selected_asset?.asset_id;
  $("saved").textContent = `已保存 · 修订 ${state.session.session.head_revision}`;
  $("candidates").replaceChildren();
  for (const item of state.candidates) { const button = element("button", ""); button.dataset.asset = item.asset_id; button.append(element("span", item.label + (item.asset_id === savedId ? " · 已选" : ""), "candidate-name"), element("span", `${item.media.duration_seconds.toFixed(3)} s · ${item.media.channels} 声道`, "candidate-meta")); button.onclick = () => activate(item.asset_id); $("candidates").append(button); }
  $("history").replaceChildren();
  for (const revision of state.session.history) { const row = element("div", "", "history-row"); row.append(element("span", `修订 ${revision.revision} · ${revision.constraints?.regions?.length || 0} 个保护区`)); if (revision.revision !== state.session.session.head_revision) { const button = element("button", "恢复此版本与规则"); button.onclick = () => mutateSelection({from_revision: revision.revision}); row.append(button); } $("history").append(row); }
  $("feedback-list").replaceChildren();
  for (const item of state.feedback) { const entry = element("div", "", "feedback-item"); entry.append(element("small", `修订 ${item.revision} · ${item.source === "agent" ? "代理备注" : "用户反馈"}`), element("span", item.text)); $("feedback-list").append(entry); }
  if (!variantId || !state.candidates.some(item => item.asset_id === variantId)) variantId = state.audition.request.candidates[0].asset_id;
  activate(state.candidates.some(item => item.asset_id === previewId) ? previewId : (savedId || variantId));
}
async function guarded(action) { try { await action(); } catch (error) { notice(error.message + " 请刷新会话后核对状态。", true); } }
async function mutateSelection(target) { return guarded(async () => { const response = await mutation("/api/select", {schema: "matter-session-select/v1", request_id: uid("select"), session_id: state.session.session.session_id, expected_revision: state.session.session.head_revision, ...target}); await refresh(); notice(`已保存为修订 ${response.revision.revision}。`); }); }
function stop() { playbackEpoch++; for (const source of playing) { try { source.stop(); } catch (_) {} source.disconnect(); } playing = []; looped = false; $("loop").setAttribute("aria-pressed", "false"); }
async function play(mode = "once") {
  stop(); const epoch = playbackEpoch, item = current(), selected = range();
  context ||= new AudioContext(); await context.resume();
  if (!buffers.has(item.asset_id)) { const response = await fetch(`/api/audio/${item.asset_id}`, {headers: {"X-Matter-Token": token}}); if (!response.ok) throw new Error("无法读取已验证的音频。"); buffers.set(item.asset_id, await context.decodeAudioData(await response.arrayBuffer())); }
  if (epoch !== playbackEpoch) return;
  const reference = state.candidates.find(c => c.asset_id === state.audition.request.reference_asset_id);
  const variant = state.candidates.find(c => c.asset_id === variantId);
  let gain = 1;
  if ($("match").checked && reference.levels.rms_dbfs !== null && variant.levels.rms_dbfs !== null && item.levels.rms_dbfs !== null) gain = Math.min(1, 10 ** ((Math.min(reference.levels.rms_dbfs, variant.levels.rms_dbfs) - item.levels.rms_dbfs) / 20));
  $("gain-info").textContent = $("match").checked ? `试听增益 ${(20 * Math.log10(gain)).toFixed(2)} dB；仅降低较响版本以匹配原版/候选的 RMS，导出不变。` : "按原始音量播放，导出保持原文件。";
  const output = context.createGain(); output.gain.value = gain; output.connect(context.destination);
  const duration = (selected.end - selected.start) / selected.rate;
  const count = mode === "repeat" ? 4 : 1;
  let remaining = count;
  for (let index = 0; index < count; index++) { const source = context.createBufferSource(); source.buffer = buffers.get(item.asset_id); source.connect(output); source.onended = () => { source.disconnect(); playing = playing.filter(item => item !== source); if (--remaining === 0) output.disconnect(); if (!playing.length) { looped = false; $("loop").setAttribute("aria-pressed", "false"); } }; if (mode === "loop") { source.loop = true; source.loopStart = selected.start / selected.rate; source.loopEnd = selected.end / selected.rate; source.start(context.currentTime, selected.start / selected.rate); } else source.start(context.currentTime + index * (duration + .2), selected.start / selected.rate, duration); playing.push(source); }
  looped = mode === "loop"; $("loop").setAttribute("aria-pressed", String(looped));
}
$("refresh").onclick = () => guarded(async () => { if (saving) return; await recoverPending(); await refresh(); });
$("start").onchange = $("end").onchange = updateRange;
$("full").onclick = () => { $("start").value = 0; $("end").value = current().media.frame_count; updateRange(); };
$("play").onclick = () => guarded(() => play()); $("stop").onclick = stop;
$("repeat").onclick = () => guarded(() => play("repeat")); $("loop").onclick = () => guarded(() => looped ? stop() : play("loop"));
$("reference").onclick = () => activate(state.audition.request.reference_asset_id, true, false);
$("variant").onclick = () => activate(variantId, true, false);
$("match").onchange = () => { stop(); $("gain-info").textContent = $("match").checked ? "下次播放将匹配原版/候选的 RMS，只衰减试听音量。" : "按原始音量播放，导出保持原文件。"; };
$("select").onclick = () => mutateSelection({asset_id: previewId});
$("save-feedback").onclick = () => guarded(async () => { const text = $("feedback").value; if (!text.trim()) throw new Error("反馈内容不能为空。"); await mutation("/api/feedback", {schema: "matter-feedback/v1", request_id: uid("feedback"), session_id: state.session.session.session_id, revision: state.session.session.head_revision, source: "user_ui", text, listening_context: `本地比较页；匹配音量开关 ${$("match").checked ? "开启" : "关闭"}。此记录不证明已播放。`}); $("feedback").value = ""; await refresh(); notice("反馈已保存到对应修订。"); });
$("export").onclick = () => guarded(async () => { const result = await mutation("/api/export", {schema: "matter-export/v1", request_id: uid("export"), session_id: state.session.session.session_id, expected_revision: state.session.session.head_revision}); const response = await fetch(`/api/export/${result.request_id}`, {headers: {"X-Matter-Token": token}}); if (!response.ok) throw new Error("导出已记录，但下载失败。"); const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a"); link.href = url; link.download = `matter-revision-${result.revision.revision}.wav`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); $("export-info").textContent = `已导出修订 ${result.revision.revision}，文件与所选版本完全一致。`; notice("导出成功。可以继续修改，已交付文件保持原样。"); });
window.addEventListener("resize", draw); window.addEventListener("pagehide", stop);
document.querySelectorAll("button, input, textarea").forEach(item => { item.disabled = item.id !== "refresh"; });
if (!token) notice("请使用 CLI 返回的完整本地比较链接打开页面。", true); else guarded(async () => { await recoverPending(); await refresh(); });
