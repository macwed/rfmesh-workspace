"use strict";
bootExposurePage({
  key: "hide",
  title: "Hide from sensors",
  subtitle: "terrain cover from red DF / ESM collectors",
  lens: "concealment", endpoint: "exposure", polarity: "both",
  placeRoles: [
    { role: "recon", label: "sensor (recon)", erps: ["low", "medium", "high"], defErp: "low" },
    { role: "df", label: "sensor (DF)", erps: ["low", "medium", "high"], defErp: "low" },
  ],
  autoSource: false,  // passive collectors aren't mesh-detectable — place by intel
  bandLabel: "Threat bands to hide from",
  presets: {
    GNSS: ["gnss_l1_gps_glonass"],
    FPV: ["ism_433", "ism_868_915", "wifi_bt_rc_fpv_2g4", "wifi_fpv_5g8"],
    comms: ["gsm_cellular_800_960", "gsm_cellular_1800_1900", "satcom_lband_inmarsat_iridium"],
  },
  defaultBands: ["wifi_fpv_5g8"],
  assetLabel: "Asset to conceal",
  assetH: 2.0,
  colors: { safe: "#2ecc71", danger: "#e74c3c" },
  legend: { safe: "lower exposure (terrain cover)", danger: "exposed to sensors" },
  disclaimer: "Relative terrain cover from red sensors — not invisibility. No power/sensitivity modelled; shadows never zero; no dBm.",
});
