// Форма эксперимента: строки вариантов с ползунками, живой индикатор трафика.
// Доли — проценты от ВСЕГО трафика аудитории; сумма может быть меньше 100
// (остаток не попадает в эксперимент), но не больше 100.

const COLORS = ["#4f46e5", "#0ea5e9", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6"];
const LETTERS = "ABCDEFGH";

const variantsBox = document.getElementById("variants");
const sumTrack = document.getElementById("sumTrack");
const sumLabel = document.getElementById("sumLabel");
const form = document.getElementById("expform");

function makeRow(name, share) {
  const row = document.createElement("div");
  row.className = "variant-row";
  row.innerHTML = `
    <input class="vname" name="variant_name" value="${name}" maxlength="16" required>
    <input class="vslider" type="range" min="0" max="100" step="1" value="${share}">
    <div class="vshare-wrap">
      <input class="vshare" name="variant_share" type="number" min="1" max="99" value="${share}" required>
      <span class="vpct">%</span>
    </div>
    <button type="button" class="vremove" title="Убрать вариант">✕</button>`;

  const slider = row.querySelector(".vslider");
  const num = row.querySelector(".vshare");
  slider.addEventListener("input", () => { num.value = slider.value; updateSum(); });
  num.addEventListener("input", () => { slider.value = num.value || 0; updateSum(); });
  row.querySelector(".vremove").addEventListener("click", () => {
    if (variantsBox.children.length > 2) { row.remove(); updateSum(); }
  });
  variantsBox.appendChild(row);
}

function totalShare() {
  return [...variantsBox.querySelectorAll(".vshare")]
    .map(x => parseInt(x.value, 10) || 0)
    .reduce((a, b) => a + b, 0);
}

function updateSum() {
  const rows = [...variantsBox.querySelectorAll(".variant-row")];
  const shares = rows.map(r => parseInt(r.querySelector(".vshare").value, 10) || 0);
  const total = shares.reduce((a, b) => a + b, 0);

  sumTrack.innerHTML = "";
  rows.forEach((r, i) => {
    const seg = document.createElement("div");
    seg.className = "sumseg";
    seg.style.width = Math.min(shares[i], 100) + "%";
    seg.style.background = COLORS[i % COLORS.length];
    seg.title = `${r.querySelector(".vname").value}: ${shares[i]}% трафика`;
    sumTrack.appendChild(seg);
    r.querySelector(".vname").style.borderLeft = `4px solid ${COLORS[i % COLORS.length]}`;
  });

  if (total > 100) {
    sumLabel.textContent = `Сумма долей ${total}% — больше 100% трафика не бывает`;
    sumLabel.className = "sumlabel bad";
  } else if (total < 2) {
    sumLabel.textContent = "Задай доли вариантов";
    sumLabel.className = "sumlabel bad";
  } else if (total === 100) {
    sumLabel.textContent = "В эксперименте: 100% трафика аудитории";
    sumLabel.className = "sumlabel ok";
  } else {
    sumLabel.textContent = `В эксперименте: ${total}% трафика аудитории · вне эксперимента: ${100 - total}%`;
    sumLabel.className = "sumlabel ok";
  }
}

document.getElementById("addVariant").addEventListener("click", () => {
  if (variantsBox.children.length >= 6) return;
  makeRow(LETTERS[variantsBox.children.length] || "X", 0);
  updateSum();
});

form.addEventListener("submit", (e) => {
  const total = totalShare();
  if (total < 2 || total > 100) {
    e.preventDefault();
    sumLabel.scrollIntoView({ behavior: "smooth", block: "center" });
    sumLabel.classList.add("shake");
    setTimeout(() => sumLabel.classList.remove("shake"), 600);
  }
});

// Префилл при редактировании: exp_form кладёт варианты в data-initial
const initial = variantsBox.dataset.initial ? JSON.parse(variantsBox.dataset.initial) : null;
if (initial && initial.length) {
  initial.forEach(v => makeRow(v.name, v.share));
} else {
  makeRow("A", 50);
  makeRow("B", 50);
}
updateSum();
