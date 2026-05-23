"use strict";
bootExposurePage({
  key: "emit",
  title: "Site an emitter",
  subtitle: "where your transmitter leaks to red vs stays quiet",
  lens: "leakage", endpoint: "exposure", polarity: "both",
  placeRoles: [
    { role: "recon", label: "sensor (recon)", erps: ["low", "medium", "high"], defErp: "low" },
    { role: "df", label: "sensor (DF)", erps: ["low", "medium", "high"], defErp: "low" },
  ],
  autoSource: false,
  bandLabel: "Your emit band",
  presets: {
    GNSS: ["gnss_l1_gps_glonass"],
    FPV: ["ism_433", "ism_868_915", "wifi_bt_rc_fpv_2g4", "wifi_fpv_5g8"],
    comms: ["gsm_cellular_800_960", "gsm_cellular_1800_1900", "satcom_lband_inmarsat_iridium"],
  },
  defaultBands: ["ism_868_915"],
  assetLabel: "Emitter site / mast",
  assetH: 10.0,  // a raised antenna leaks further than a ground asset
  colors: { safe: "#2ecc71", danger: "#e74c3c" },
  legend: { safe: "low leakage toward red", danger: "emission reaches red" },
  disclaimer: "Relative terrain attenuation of YOUR emission toward red sensors — geometry only. ERP/antenna gain not modelled; shadows never zero; no dBm.",
});
