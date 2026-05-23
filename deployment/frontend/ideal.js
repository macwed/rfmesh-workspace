"use strict";
bootExposurePage({
  key: "ideal",
  title: "Ideal site",
  subtitle: "hidden from sensors AND your links survive",
  lens: "combined", endpoint: "exposure/combined", polarity: "safe",  // single green "good on both"
  placeRoles: [
    { role: "jammer", label: "jammer", erps: ["low", "medium", "high", "very_high"], defErp: "medium" },
    { role: "recon", label: "sensor (recon)", erps: ["low", "medium", "high"], defErp: "low" },
    { role: "df", label: "sensor (DF)", erps: ["low", "medium", "high"], defErp: "low" },
  ],
  autoSource: true,
  bandLabel: "Threat bands + your links",
  presets: {
    GNSS: ["gnss_l1_gps_glonass"],
    FPV: ["ism_433", "ism_868_915", "wifi_bt_rc_fpv_2g4", "wifi_fpv_5g8"],
    comms: ["gsm_cellular_800_960", "gsm_cellular_1800_1900", "satcom_lband_inmarsat_iridium"],
  },
  defaultBands: ["wifi_fpv_5g8"],
  assetLabel: "Candidate area",
  colors: { safe: "#7ad06b", danger: "#e74c3c" },
  legend: { safe: "good on both (concealed + link survives)", danger: "fails one or both" },
  disclaimer: "Intersection of concealment and jam-shadow — relative cover only, not a safe-zone promise. Needs ≥1 sensor AND ≥1 jammer placed. No dBm.",
});
