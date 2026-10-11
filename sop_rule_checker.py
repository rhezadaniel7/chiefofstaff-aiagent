# SOP Rule Checker — Custom Component Langflow (ChiefOfStaff AI)
# Cara pakai: di Langflow klik "+ New Custom Component" (atau drag "Custom Component"),
# hapus kode bawaan, tempel seluruh file ini, klik Check & Save.
# Lalu aktifkan "Tool Mode" pada komponen dan sambungkan output Toolset ke port "Tools" milik Agent.
#
# Fungsi: pemeriksaan DETERMINISTIK (bukan tebakan LLM) atas angka & aturan wajib SOP pengadaan.
# LLM tetap dipakai untuk memahami bahasa dan mengutip pasal dari Astra DB,
# tetapi keputusan LOLOS / DITOLAK diambil oleh kode di bawah ini.

import json
from datetime import date, datetime

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MultilineInput
from lfx.schema.data import Data
from lfx.template.field.base import Output

DEFAULT_RULES = {
    "sop_id": "SOP-PGD-01 (contoh fiktif)",
    "wajib": [
        "pemohon", "divisi", "nama_barang", "jumlah", "harga_satuan",
        "vendor", "alasan", "tanggal_dibutuhkan", "sisa_anggaran_divisi", "jumlah_penawaran",
    ],
    "tier": [
        {"maks": 1_000_000, "approver": "Kepala Divisi", "min_penawaran": 1, "pasal": "Pasal 3.1"},
        {"maks": 10_000_000, "approver": "Manajer Operasional", "min_penawaran": 2, "pasal": "Pasal 3.2"},
        {"maks": 50_000_000, "approver": "Direktur/Pemilik", "min_penawaran": 3, "pasal": "Pasal 3.3"},
    ],
    "batas_maks": 50_000_000,
    "pasal_batas_maks": "Pasal 3.4",
    "pasal_anggaran": "Pasal 4.1",
    "vendor_dilarang": ["CV Maju Mundur", "PT Fiktif Sejahtera"],
    "pasal_vendor": "Pasal 5.2",
    "lead_time_hari": 3,
    "pasal_lead_time": "Pasal 6.1",
}


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace("rp", "").replace(".", "").replace(",", ".").strip()
    return float(s)


def audit(pengajuan: dict, rules: dict, today: date | None = None) -> dict:
    """Return {status, temuan, ...}. Status: KURANG_DATA | TIDAK_PATUH | DITOLAK | LOLOS."""
    today = today or date.today()
    kosong = [f for f in rules["wajib"] if pengajuan.get(f) in (None, "", [])]
    if kosong:
        return {
            "status": "KURANG_DATA",
            "field_kurang": kosong,
            "instruksi_agent": "Tanyakan field yang kurang satu per satu. Jangan menebak nilainya.",
        }

    try:
        jumlah = _num(pengajuan["jumlah"])
        harga = _num(pengajuan["harga_satuan"])
        sisa = _num(pengajuan["sisa_anggaran_divisi"])
        n_penawaran = int(_num(pengajuan["jumlah_penawaran"]))
    except (ValueError, TypeError):
        return {"status": "KURANG_DATA", "field_kurang": ["format angka tidak valid"],
                "instruksi_agent": "Minta pengguna menulis ulang angka (contoh: 2500000)."}

    total = jumlah * harga
    fatal, perbaikan = [], []

    if total > rules["batas_maks"]:
        fatal.append(f"Total Rp{total:,.0f} melebihi batas alur ini Rp{rules['batas_maks']:,.0f} ({rules['pasal_batas_maks']}).")
    if total > sisa:
        fatal.append(f"Total Rp{total:,.0f} melebihi sisa anggaran divisi Rp{sisa:,.0f} ({rules['pasal_anggaran']}).")
    vendor = str(pengajuan["vendor"]).strip().lower()
    if vendor in [v.lower() for v in rules["vendor_dilarang"]]:
        fatal.append(f"Vendor '{pengajuan['vendor']}' ada di daftar vendor dilarang ({rules['pasal_vendor']}).")

    tier = next((t for t in rules["tier"] if total <= t["maks"]), rules["tier"][-1])
    if n_penawaran < tier["min_penawaran"]:
        perbaikan.append(f"Butuh minimal {tier['min_penawaran']} penawaran vendor untuk nominal ini, baru ada {n_penawaran} ({tier['pasal']}).")

    try:
        tgl = datetime.strptime(str(pengajuan["tanggal_dibutuhkan"]), "%Y-%m-%d").date()
        if tgl < today:
            return {
                "status": "KURANG_DATA",
                "field_kurang": ["tanggal_dibutuhkan"],
                "instruksi_agent": (
                    f"Tanggal {tgl.isoformat()} sudah lewat (hari ini {today.isoformat()}). "
                    "Kemungkinan tahun salah. Konfirmasi tanggal lengkap (YYYY-MM-DD) ke pengguna."
                ),
            }
        if (tgl - today).days < rules["lead_time_hari"]:
            perbaikan.append(f"Tanggal dibutuhkan kurang dari {rules['lead_time_hari']} hari dari hari ini ({rules['pasal_lead_time']}).")
    except ValueError:
        perbaikan.append("Format tanggal_dibutuhkan harus YYYY-MM-DD.")

    base = {"total": total, "approver_wajib": tier["approver"], "sop": rules["sop_id"]}
    if fatal:
        return {**base, "status": "DITOLAK", "temuan": fatal + perbaikan,
                "instruksi_agent": "BLOCKED: jangan panggil tool Gmail/Calendar. Jelaskan pasal yang dilanggar dan arahkan ke manusia."}
    if perbaikan:
        return {**base, "status": "TIDAK_PATUH", "temuan": perbaikan,
                "instruksi_agent": "Minta pengguna memperbaiki temuan. Jangan buat draf sebelum status LOLOS."}
    return {**base, "status": "LOLOS", "temuan": [],
            "instruksi_agent": "Tampilkan ringkasan, minta pengguna mengetik KONFIRMASI sebelum membuat draf."}


class SOPRuleChecker(Component):
    display_name = "SOP Rule Checker"
    description = (
        "Audit deterministik pengajuan pembelian terhadap SOP pengadaan. "
        "Input: JSON pengajuan dengan field pemohon, divisi, nama_barang, jumlah, harga_satuan, vendor, "
        "alasan, tanggal_dibutuhkan (YYYY-MM-DD), sisa_anggaran_divisi, jumlah_penawaran. "
        "Output: status KURANG_DATA / TIDAK_PATUH / DITOLAK / LOLOS beserta pasal terkait."
    )
    icon = "shield-check"
    name = "SOPRuleChecker"

    inputs = [
        MultilineInput(
            name="pengajuan_json",
            display_name="Pengajuan (JSON)",
            info="Data pengajuan dalam format JSON. Field yang belum diketahui isi null.",
            tool_mode=True,
            required=True,
        ),
        MultilineInput(
            name="rules_json",
            display_name="Aturan SOP (JSON)",
            info="Parameter angka SOP. Samakan dengan dokumen SOP yang di-ingest ke Astra DB.",
            value=json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2),
            advanced=True,
        ),
    ]

    outputs = [Output(display_name="Hasil Audit", name="hasil_audit", method="run_audit")]

    def run_audit(self) -> Data:
        try:
            pengajuan = json.loads(self.pengajuan_json)
        except (json.JSONDecodeError, TypeError):
            result = {"status": "KURANG_DATA", "field_kurang": ["pengajuan_json bukan JSON valid"]}
        else:
            rules = json.loads(self.rules_json) if self.rules_json else DEFAULT_RULES
            result = audit(pengajuan, rules)
        self.status = result["status"]
        return Data(data=result)
