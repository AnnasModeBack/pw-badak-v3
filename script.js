// --- Deklarasi Variabel ---
const homePage = document.getElementById("homePage");
const paymentPage = document.getElementById("paymentPage");
const togglePaymentBtn = document.getElementById("togglePaymentBtn");
const paymentInfo = document.getElementById("paymentInfo");
const qrisContainer = document.getElementById("qrisContainer");
const qrDownloadBtn = document.getElementById("qrDownloadBtn");
const qrisImage = document.getElementById("qrisImage");
const copyDanaBtn = document.getElementById("copyDanaBtn");
const danaNumber = document.getElementById("danaNumber");
const copiedMessage = document.getElementById("copiedMessage");
const confirmCopyBtn = document.getElementById("confirmCopyBtn");

// --- Fungsi Navigasi Halaman ---
function goToPayment() {
  homePage.classList.add("fade-out");
  setTimeout(() => {
    homePage.classList.add("hidden");
    homePage.classList.remove("fade-out");
    paymentPage.classList.remove("hidden");
    paymentPage.classList.add("fade-in");
  }, 400);
}

function backToHome() {
  paymentPage.classList.add("fade-out");
  setTimeout(() => {
    paymentPage.classList.add("hidden");
    paymentPage.classList.remove("fade-out");
    homePage.classList.remove("hidden");
    homePage.classList.add("fade-in");
  }, 400);
}

// --- Event Listener ---

// Toggle bagian pembayaran
togglePaymentBtn.addEventListener("click", () => {
  paymentInfo.classList.toggle("show");
});

// Memperbesar QRIS dan menampilkan tombol download
qrisContainer.addEventListener("click", () => {
  qrisContainer.classList.toggle("expanded");
  if (qrisContainer.classList.contains("expanded")) {
    qrDownloadBtn.style.display = "inline-block";
  } else {
    qrDownloadBtn.style.display = "none";
  }
});

// Fungsi Download QR
qrDownloadBtn.addEventListener("click", (event) => {
  event.stopPropagation(); // Mencegah event dari qrisContainer terpicu
  const link = document.createElement("a");
  link.href = qrisImage.src;
  link.download = "qris-dixey.png";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  alert("✅ QRIS berhasil diunduh.\nCek folder 'Download' HP kamu.");
});

// Menyalin nomor DANA
copyDanaBtn.addEventListener("click", () => {
  const danaText = danaNumber.innerText;
  navigator.clipboard.writeText(danaText)
    .then(() => {
      copiedMessage.classList.remove("hidden");
    })
    .catch(err => {
      alert('Gagal menyalin nomor: ' + err);
    });
});

// Menyembunyikan pesan sukses salin
confirmCopyBtn.addEventListener("click", () => {
  copiedMessage.classList.add("hidden");
});
