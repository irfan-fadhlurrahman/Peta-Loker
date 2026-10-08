/* Peta Loker dashboard. Plain JS: reads the JSON snapshot in ./data/ and
 * renders whichever page is open (body[data-page]). All text from the data
 * is escaped before it touches the DOM — it originates from scraped pages. */
"use strict";

const NF = new Intl.NumberFormat("id-ID");
const PCT = new Intl.NumberFormat("id-ID", { style: "percent", maximumFractionDigits: 1 });
const DAY = new Intl.DateTimeFormat("id-ID", { day: "numeric", month: "short" });

const EDUCATION = { "SD": "SD", "SMP": "SMP", "SMA/SMK": "SMA/SMK", "D1-D3": "Diploma (D1–D3)",
  "D4/S1": "Sarjana / D4", "S2": "Magister (S2)", "S3": "Doktor (S3)" };
const EMPLOYMENT = { full_time: "Penuh waktu", part_time: "Paruh waktu", contract: "Kontrak",
  internship: "Magang", freelance: "Lepas" };
const TEAL = ["#E9F2F0", "#9CCFC8", "#4FA39A", "#0E6B66", "#073F3C"];

// Tile map: province code -> [abbreviation, column, row] (approximate geography).
const TILES = {
  "11": ["ACE", 1, 1], "12": ["SUT", 2, 1], "21": ["KRI", 4, 1], "13": ["SBR", 2, 2], "14": ["RIA", 3, 2],
  "17": ["BKL", 2, 3], "15": ["JAM", 3, 3], "19": ["BBL", 5, 3], "16": ["SSL", 3, 4], "18": ["LPG", 4, 4],
  "31": ["DKI", 5, 4], "36": ["BTN", 4, 5], "32": ["JBR", 5, 5], "33": ["JTG", 6, 5], "34": ["DIY", 6, 6],
  "35": ["JTM", 7, 5], "51": ["BAL", 8, 5], "52": ["NTB", 9, 5], "53": ["NTT", 10, 5],
  "61": ["KBR", 6, 2], "62": ["KTG", 7, 3], "63": ["KSL", 8, 3], "64": ["KTM", 8, 2], "65": ["KTR", 8, 1],
  "75": ["GOR", 10, 1], "71": ["SUL", 11, 1], "72": ["STG", 10, 2], "76": ["SBA", 9, 3], "73": ["SSE", 10, 3],
  "74": ["SGR", 11, 3], "82": ["MAU", 13, 1], "81": ["MAL", 13, 3], "92": ["PBD", 14, 1], "91": ["PBR", 15, 1],
  "94": ["PAP", 17, 1], "96": ["PTG", 16, 2], "97": ["PPG", 17, 2], "95": ["PSL", 17, 3],
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Masked tokens become a visible redaction bar instead of literal text.
function maskedText(text) {
  return esc(text).replaceAll("[disamarkan]", '<span class="mask" title="disamarkan">xxxxxxxx</span>')
    .replaceAll("[perusahaan]", '<span class="mask" title="nama perusahaan disamarkan">xxxxxx</span>');
}
function jt(n) {
  const v = n / 1e6;
  return (v >= 10 ? Math.round(v) : Math.round(v * 10) / 10).toLocaleString("id-ID");
}
function salaryText(s) {
  if (!s) return "Gaji tidak dicantumkan";
  return s.max && s.max !== s.min ? `Rp${jt(s.min)}–${jt(s.max)} jt/bulan` : `Rp${jt(s.min)} jt/bulan`;
}
function dayLabel(iso) {
  return iso ? DAY.format(new Date(iso + "T00:00:00")) : "–";
}
async function load(name) {
  const response = await fetch(`data/${name}.json`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${name}.json: HTTP ${response.status}`);
  return response.json();
}
function setLabel(summary) {
  const el = document.getElementById("data-label");
  if (!el) return;
  el.textContent = summary.data_label === "demo" ? "Data contoh (sintetis)" : "Data nyata · disamarkan";
  const updated = document.getElementById("updated");
  if (updated) updated.textContent = `Snapshot ${new Date(summary.generated_at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}`;
}
function bar(pct, color = "") {
  return `<span class="bar-track"><span class="bar-fill" style="width:${Math.max(0, Math.min(100, pct))}%;${color}"></span></span>`;
}
function fail(error) {
  const main = document.querySelector("main");
  main.insertAdjacentHTML("afterbegin", `<div class="card border-danger text-danger">Data dashboard belum tersedia (${esc(error.message)}). Jalankan <code>make demo</code> atau <code>make dashboard</code>, lalu <code>make dashboard-serve</code>.</div>`);
}

// ------------------------------------------------------------- Ringkasan
async function ringkasan() {
  const [summary, provinces, kbji, trend, skills, salary] = await Promise.all(
    ["summary", "by_province", "by_kbji", "trend", "skills", "salary"].map(load));
  setLabel(summary);

  const stats = [
    ["Lowongan aktif", NF.format(summary.active_vacancies), "unik, setelah deduplikasi"],
    ["Lowongan baru", NF.format(summary.new_last_7_days), "7 hari terakhir"],
    ["Duplikat digabung", PCT.format(summary.duplicate_ratio), `dari ${NF.format(summary.postings)} postingan`],
    ["Terkode KBJI", PCT.format(summary.coded_ratio), "lowongan aktif yang sudah dikode"],
    ["Sumber berhasil", `${summary.sources_ok} / ${summary.sources_total}`, "pada run terakhir"],
  ];
  document.getElementById("stats").innerHTML = stats.map(([label, value, note]) => `
    <div class="card px-5 py-5 flex flex-col gap-1.5"><span class="cap font-semibold">${label}</span>
    <span class="stat-value">${value}</span><span class="cap">${note}</span></div>`).join("");

  // Tile map with five quantile bins of the non-zero counts.
  const counts = provinces.provinces.map((p) => p.n).filter((n) => n > 0).sort((a, b) => a - b);
  const cut = (q) => counts.length ? counts[Math.min(counts.length - 1, Math.floor(q * counts.length))] : 0;
  const bins = [cut(0.2), cut(0.4), cut(0.6), cut(0.8)];
  const map = document.getElementById("tilemap");
  map.style.gridTemplateColumns = "repeat(17, minmax(30px, 1fr))";
  map.innerHTML = provinces.provinces.map((p) => {
    const [abbr, col, row] = TILES[p.code] || [p.code, 1, 7];
    const bin = p.n === 0 ? -1 : bins.filter((b) => p.n > b).length;
    const bg = bin < 0 ? "#F1EEE7" : TEAL[bin];
    const fg = bin >= 3 ? "#FFFFFF" : "#1B1D1F";
    return `<span class="tile aspect-square" style="grid-column:${col};grid-row:${row};background:${bg};color:${fg}"
      title="${esc(p.name)}: ${NF.format(p.n)} lowongan">${abbr}</span>`;
  }).join("");
  document.getElementById("map-note").textContent =
    `${NF.format(provinces.remote)} jarak jauh · ${NF.format(provinces.unmapped)} tanpa lokasi`;

  const top = [...provinces.provinces].sort((a, b) => b.n - a.n).slice(0, 10);
  const topMax = top[0]?.n || 1;
  document.getElementById("top-provinces").innerHTML = top.map((p) => `
    <div class="grid grid-cols-[150px_minmax(0,1fr)_64px] items-center gap-3">
      <span class="text-sm">${esc(p.name)}</span>${bar((p.n / topMax) * 100)}
      <span class="font-mono text-[13px] text-right">${NF.format(p.n)}</span></div>`).join("");

  const coded = kbji.coded || 0;
  const majorMax = Math.max(1, ...kbji.majors.map((m) => m.n));
  document.getElementById("kbji-note").textContent = `% dari ${NF.format(coded)} lowongan yang sudah dikode`;
  document.getElementById("kbji-majors").innerHTML = kbji.majors.map((m) => `
    <div class="grid grid-cols-[28px_minmax(0,220px)_minmax(0,1fr)_52px] items-center gap-3">
      <span class="font-mono text-[13px] font-medium text-teal bg-teal-soft rounded-md text-center leading-6">${esc(m.code)}</span>
      <span class="text-sm leading-[18px]">${esc(m.title)}</span>${bar((m.n / majorMax) * 100)}
      <span class="font-mono text-[13px] text-right">${coded ? PCT.format(m.n / coded) : "–"}</span></div>`).join("");
  document.getElementById("kbji-units").innerHTML = kbji.top_units.length ? kbji.top_units.map((u) => `
    <div class="grid grid-cols-[48px_minmax(0,1fr)_56px] items-center gap-3">
      <span class="font-mono text-[13px] text-teal">${esc(u.code)}</span><span class="text-sm">${esc(u.title)}</span>
      <span class="font-mono text-[13px] text-right">${NF.format(u.n)}</span></div>`).join("")
    : '<span class="cap">Belum ada lowongan yang dikode.</span>';

  if (window.Chart) {
    new Chart(document.getElementById("trend"), {
      type: "line",
      data: { labels: trend.days.map((d) => dayLabel(d.date)),
        datasets: [{ data: trend.days.map((d) => d.new), borderColor: "#0E6B66", backgroundColor: "rgba(14,107,102,.08)",
          fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2.5 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6, color: "#5B605C" } },
          y: { beginAtZero: true, grid: { color: "#EEEBE4" }, ticks: { color: "#5B605C", precision: 0 } } } },
    });
  }

  document.getElementById("skills").innerHTML = skills.top.length ? skills.top.map((s) =>
    `<li class="chip">${esc(s.skill)} <span class="font-mono text-xs text-ink-muted">${NF.format(s.n)}</span></li>`).join("")
    : '<li class="cap">Belum ada keterampilan yang diekstraksi.</li>';

  const scaleMax = Math.max(1, ...salary.levels.map((l) => l.p75)) * 1.25;
  document.getElementById("salary").innerHTML = salary.levels.length ? salary.levels.map((l) => `
    <div class="grid grid-cols-[110px_minmax(0,1fr)_96px] items-center gap-3">
      <span class="text-sm font-semibold">${esc(EDUCATION[l.education] || l.education)}</span>
      <span class="relative h-[18px] bg-[#F1EEE7] rounded block">
        <span class="absolute top-[3px] h-3 rounded-sm bg-teal-light block" style="left:${(l.p25 / scaleMax) * 100}%;width:${((l.p75 - l.p25) / scaleMax) * 100}%"></span>
        <span class="absolute top-0 h-[18px] w-[3px] rounded-sm bg-teal-deep block" style="left:${(l.median / scaleMax) * 100}%"></span>
      </span>
      <span class="font-mono text-[13px] text-right" title="n = ${l.n}">${jt(l.p25)}–${jt(l.p75)}</span></div>`).join("")
    : '<span class="cap">Belum cukup lowongan bergaji untuk ditampilkan.</span>';
}

// --------------------------------------------------------------- Lowongan
const PAGE_SIZE = 20;
async function lowongan() {
  const [summary, data, kbji] = await Promise.all(["summary", "vacancies", "by_kbji"].map(load));
  setLabel(summary);
  const all = data.vacancies;
  const state = { page: 1 };

  const checkbox = (name, value, label) =>
    `<label class="chk"><input type="checkbox" name="${name}" value="${esc(value)}"> ${esc(label)}</label>`;
  document.getElementById("f-major").insertAdjacentHTML("beforeend",
    kbji.majors.filter((m) => m.n > 0).map((m) => checkbox("major", m.code, `${m.code} · ${m.title}`)).join(""));
  document.getElementById("f-edu").insertAdjacentHTML("beforeend",
    Object.entries(EDUCATION).map(([k, v]) => checkbox("edu", k, v)).join(""));
  document.getElementById("f-emp").insertAdjacentHTML("beforeend",
    Object.entries(EMPLOYMENT).map(([k, v]) => checkbox("emp", k, v)).join(""));

  const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
  function filtered() {
    const q = document.getElementById("q").value.trim().toLowerCase();
    const loc = document.getElementById("loc").value.trim().toLowerCase();
    const majors = checked("major"), edus = checked("edu"), emps = checked("emp");
    const onlySalary = document.getElementById("f-salary").checked;
    const multi = document.getElementById("f-multi").checked;
    const review = document.getElementById("f-review").checked;
    const rows = all.filter((v) => {
      const hay = [v.title, v.kbji?.code, v.kbji?.title, v.kbji?.unit, ...(v.skills || [])].join(" ").toLowerCase();
      if (q && !hay.includes(q)) return false;
      if (loc && !`${v.region?.name || ""} ${v.region?.province || ""} ${v.remote ? "remote jarak jauh" : ""}`.toLowerCase().includes(loc)) return false;
      if (majors.length && !majors.includes(v.kbji?.unit?.[0])) return false;
      if (edus.length && !edus.includes(v.education)) return false;
      if (emps.length && !emps.includes(v.employment_type)) return false;
      if (onlySalary && !v.salary) return false;
      if (multi && v.n_sources < 2) return false;
      if (review && !v.kbji?.needs_review) return false;
      return true;
    });
    const sort = document.getElementById("sort").value;
    const key = { new: (v) => v.last_seen, salary: (v) => v.salary?.max || 0, sources: (v) => v.n_sources,
      confidence: (v) => -(v.kbji?.confidence ?? 1) };
    return rows.sort((a, b) => (key[sort](b) > key[sort](a) ? 1 : key[sort](b) < key[sort](a) ? -1 : 0));
  }

  function card(v) {
    const conf = v.kbji?.confidence;
    const low = v.kbji?.needs_review;
    const facts = [
      ["Lokasi", v.remote && !v.region ? "Jarak jauh" : v.region ? `${esc(v.region.name)} <span class="font-mono cap">${esc(v.region.code)}</span>` : "–"],
      ["Gaji", esc(salaryText(v.salary))],
      ["Pendidikan", esc(EDUCATION[v.education] || "–")],
      ["Pengalaman", v.experience_years == null ? "–" : v.experience_years === 0 ? "Tanpa pengalaman" : `${v.experience_years}+ tahun`],
      ["Tipe kerja", esc(EMPLOYMENT[v.employment_type] || "–")],
      ["Tayang di", `${v.n_sources} sumber`],
      ["Pertama / terakhir dilihat", `${dayLabel(v.first_seen)} – ${dayLabel(v.last_seen)}`],
    ];
    return `<article class="bg-surface border border-line rounded-xl px-6 py-5 flex flex-col gap-3">
      <div class="flex flex-wrap justify-between gap-3">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="m-0 text-lg leading-6 font-semibold">${maskedText(v.title)}</h2>
          <span class="text-sm text-ink-soft">Perusahaan <span class="font-mono">${esc(v.company || "–")}</span>${v.kbli ? ` · ${esc(v.kbli.title)} (KBLI ${esc(v.kbli.section)})` : ""}</span>
        </div>
        <div class="flex items-start gap-2 flex-wrap">
          ${v.kbji ? `<span class="badge text-teal-dark bg-teal-soft"><span class="font-mono">KBJI ${esc(v.kbji.code || v.kbji.unit)}</span> · ${esc(v.kbji.title || v.kbji.unit_title)}</span>` : '<span class="badge text-ink-soft bg-[#F1EEE7]">Belum dikode</span>'}
          ${conf != null ? `<span class="badge ${low ? "text-amber bg-amber-bg" : "text-ink-soft bg-[#F1EEE7]"}">${low ? "Perlu tinjauan" : "Yakin"} ${conf.toFixed(2).replace(".", ",")}</span>` : ""}
        </div>
      </div>
      <dl class="m-0 grid gap-x-5 gap-y-3 grid-cols-[repeat(auto-fit,minmax(min(150px,100%),1fr))]">
        ${facts.map(([k, val]) => `<div><dt class="cap">${k}</dt><dd class="m-0 text-sm">${val}</dd></div>`).join("")}
      </dl>
      <details class="border-t border-line-soft pt-3">
        <summary class="cursor-pointer text-sm font-semibold text-teal min-h-8">Rincian</summary>
        <div class="pt-3 grid gap-5 grid-cols-[repeat(auto-fit,minmax(min(300px,100%),1fr))]">
          <div class="flex flex-col gap-2"><span class="lbl">Cuplikan deskripsi (disamarkan)</span>
            <p class="m-0 text-sm leading-[22px] text-ink-soft">${maskedText(v.description)}</p>
            <span class="cap">Kontak dan nama perusahaan dihapus. Teks penuh tidak ditampilkan.</span></div>
          <div class="flex flex-col gap-2.5">
            <span class="lbl">Keterampilan</span>
            <ul class="list-none m-0 p-0 flex flex-wrap gap-1.5">${(v.skills || []).map((s) => `<li class="chip !py-1 !px-2.5 !text-[13px]">${esc(s)}</li>`).join("") || '<li class="cap">–</li>'}</ul>
            ${v.kbji?.reason ? `<span class="lbl mt-1.5">Alasan kode KBJI (LLM)</span><p class="m-0 text-sm text-ink-soft">${esc(v.kbji.reason)}</p>` : ""}
          </div>
        </div>
      </details>
    </article>`;
  }

  function render() {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * PAGE_SIZE;
    document.getElementById("count").textContent = NF.format(rows.length);
    document.getElementById("list").innerHTML = rows.slice(start, start + PAGE_SIZE).map(card).join("")
      || '<div class="card text-center flex flex-col gap-2"><strong>Tidak ada lowongan yang cocok.</strong><span class="cap">Ubah kata kunci atau atur ulang filter.</span></div>';
    document.getElementById("showing").textContent = rows.length
      ? `Menampilkan ${NF.format(start + 1)}–${NF.format(Math.min(start + PAGE_SIZE, rows.length))} dari ${NF.format(rows.length)} lowongan (snapshot memuat ${NF.format(data.shown)} dari ${NF.format(data.total_active)} lowongan aktif)`
      : "";
    const pager = document.getElementById("pager");
    const btn = (label, page, current = false, aria = "") =>
      `<button type="button" data-page="${page}" ${aria ? `aria-label="${aria}"` : ""} ${current ? 'aria-current="page"' : ""}
        class="min-w-11 h-11 rounded-lg border ${current ? "bg-teal text-white border-teal font-semibold" : "bg-surface border-line text-ink-soft"}"
        ${page < 1 || page > pages ? "disabled" : ""}>${label}</button>`;
    const nums = [...new Set([1, state.page - 1, state.page, state.page + 1, pages])].filter((p) => p >= 1 && p <= pages);
    pager.innerHTML = btn("‹", state.page - 1, false, "Halaman sebelumnya") + nums.map((p) => btn(p, p, p === state.page)).join("")
      + btn("›", state.page + 1, false, "Halaman berikutnya");
  }

  document.getElementById("pager").addEventListener("click", (e) => {
    const page = Number(e.target.closest("button")?.dataset.page);
    if (page) { state.page = page; render(); window.scrollTo({ top: 0, behavior: "smooth" }); }
  });
  document.querySelector("aside").addEventListener("input", () => { state.page = 1; render(); });
  document.getElementById("sort").addEventListener("change", () => { state.page = 1; render(); });
  document.getElementById("reset").addEventListener("click", () => {
    document.querySelectorAll("aside input").forEach((i) => { if (i.type === "checkbox") i.checked = false; else i.value = ""; });
    state.page = 1; render();
  });
  render();
}

// ------------------------------------------------------------ Operasional
const STATUS = {
  success: ["Berhasil", "✓", "text-teal-dark bg-ok-bg", "#4FA39A"],
  partial: ["Sebagian", "!", "text-amber bg-amber-bg", "#C8781E"],
  failed: ["Gagal", "✕", "text-danger bg-danger-bg", "#B3261E"],
  blocked: ["Diblokir", "✕", "text-danger bg-danger-bg", "#B3261E"],
  running: ["Berjalan", "…", "text-ink-soft bg-[#F1EEE7]", "#BDB7AA"],
};
async function operasional() {
  const [summary, ops] = await Promise.all(["summary", "ops"].map(load));
  setLabel(summary);
  const q = ops.quality;
  const tokens = ops.llm.steps.reduce((acc, s) => acc + (s.input_tokens || 0) + (s.output_tokens || 0), 0);
  const stats = [
    ["Sumber berhasil", `${summary.sources_ok} / ${summary.sources_total}`, "pada run terakhir"],
    ["Postingan tersimpan", NF.format(summary.postings), `${NF.format(summary.active_vacancies)} lowongan aktif`],
    ["Gerbang kualitas", q ? (q.status === "pass" ? "Lolos" : "Gagal") : "–", q ? `${q.checks.filter((c) => c.status === "pass").length} cek lolos · ${q.warn.length} peringatan` : "belum dijalankan"],
    ["Token LLM", tokens ? NF.format(tokens) : "–", "total semua langkah"],
  ];
  document.getElementById("ops-stats").innerHTML = stats.map(([label, value, note]) => `
    <div class="card px-5 py-5 flex flex-col gap-1.5"><span class="cap font-semibold">${label}</span>
    <span class="stat-value">${value}</span><span class="cap">${note}</span></div>`).join("");

  document.getElementById("sources").innerHTML = ops.sources.map((s) => {
    const last = s.runs[0] || {};
    const [label, icon, cls] = STATUS[last.status] || STATUS.running;
    const history = [...s.runs].reverse().map((r) => {
      const color = (STATUS[r.status] || STATUS.running)[3];
      const h = r.status === "success" ? 20 : r.status === "partial" ? 12 : 6;
      return `<span class="block w-1.5 rounded-sm" style="height:${h}px;background:${color}" title="${esc(r.started_at)}: ${esc(r.status)}"></span>`;
    }).join("");
    return `<tr>
      <td class="py-3 px-3 border-b border-line-soft font-semibold">${esc(s.source)}</td>
      <td class="py-3 px-3 border-b border-line-soft font-mono text-[13px]">${esc(s.class)}</td>
      <td class="py-3 px-3 border-b border-line-soft font-mono text-[13px] whitespace-nowrap">${esc((last.started_at || "").replace("T", " ").slice(0, 16))}</td>
      <td class="py-3 px-3 border-b border-line-soft"><span class="pill ${cls}"><span aria-hidden="true">${icon}</span>${label}${last.error_type ? ` · ${esc(last.error_type)}` : ""}</span></td>
      <td class="py-3 px-3 border-b border-line-soft font-mono text-[13px] text-right">${NF.format(last.n_saved || 0)}</td>
      <td class="py-3 px-3 border-b border-line-soft"><span class="flex gap-0.5 items-end h-5">${history}</span></td></tr>`;
  }).join("") || '<tr><td colspan="6" class="py-3 px-3 cap">Belum ada run.</td></tr>';

  document.getElementById("checks").innerHTML = q ? q.checks.map((c) => {
    const [label, cls] = c.status === "pass" ? ["Lolos", "text-teal-dark bg-ok-bg"]
      : c.status === "warn" ? ["Peringatan", "text-amber bg-amber-bg"] : ["Gagal", "text-danger bg-danger-bg"];
    const value = Array.isArray(c.value) ? (c.value.join(", ") || "–") : typeof c.value === "number" && c.value <= 1 && !Number.isInteger(c.value) ? PCT.format(c.value) : c.value;
    return `<li class="grid grid-cols-[100px_minmax(0,1fr)_auto] gap-3 items-center py-2.5 border-b border-line-soft">
      <span class="pill ${cls} justify-center">${label}</span><span class="text-sm font-mono">${esc(c.check)}</span>
      <span class="font-mono text-[13px] text-ink-soft">${esc(value)}</span></li>`;
  }).join("") : '<li class="cap py-2">Gerbang kualitas belum dijalankan.</li>';

  const hist = ops.llm.confidence_histogram;
  const hmax = Math.max(1, ...hist);
  const thr = ops.llm.review_threshold;
  document.getElementById("histogram").innerHTML = hist.map((n, i) =>
    `<span class="flex-1 rounded-t-[3px] block" style="height:${(n / hmax) * 100}%;background:${(i + 1) / 10 <= thr ? "#C8781E" : i === 9 ? "#0E6B66" : "#4FA39A"}" title="${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${n}"></span>`).join("");
  document.getElementById("threshold-label").textContent = `ambang tinjauan ${String(thr).replace(".", ",")}`;
  document.getElementById("conf-note").textContent = `${NF.format(hist.reduce((a, b) => a + b, 0))} lowongan dikode`;
  document.getElementById("review").innerHTML = `<span class="text-sm"><b>${NF.format(ops.llm.needs_review)} lowongan</b> di bawah ambang atau bermasalah menunggu tinjauan manual</span>
    <a href="lowongan.html" class="font-semibold min-h-11 inline-flex items-center">Buka daftar</a>`;

  document.getElementById("llm").innerHTML = ops.llm.steps.map((s) => `<tr>
    <td class="py-2.5 px-3 border-b border-line-soft font-mono">${esc(s.step)}</td>
    <td class="py-2.5 px-3 border-b border-line-soft text-right font-mono">${NF.format(s.calls)}</td>
    <td class="py-2.5 px-3 border-b border-line-soft text-right font-mono">${NF.format(s.items || 0)}</td>
    <td class="py-2.5 px-3 border-b border-line-soft text-right font-mono">${NF.format(s.input_tokens || 0)}</td>
    <td class="py-2.5 px-3 border-b border-line-soft text-right font-mono">${NF.format(s.output_tokens || 0)}</td></tr>`).join("")
    || '<tr><td colspan="5" class="py-3 px-3 cap">Belum ada panggilan LLM.</td></tr>';
}

const PAGES = { ringkasan, lowongan, operasional };
document.addEventListener("DOMContentLoaded", () => {
  const run = PAGES[document.body.dataset.page];
  if (run) run().catch(fail);
});
