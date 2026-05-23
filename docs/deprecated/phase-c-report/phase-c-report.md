# Phase C report

I had some problems with Phase C Bench. It turned out that the GSM mast close to my house (about 650m) transmits signal weaker than a mast farther away in the other town.
Despite living in a rural area there are many transimitters on ~900-980MHz band. So checked maps btsearch.pl for other GSM Masts.
The second one (Mast B) seemed to be quite isolated but again, there was stronger signal from the side (larger town ~5km away) than from the Mast B (about 2.2km) even with the antenna pointing at it.
Then I decied to look at the heatmap and chose the strongest transmitter nearby (Mast C) - also in other town few kilometers away. I drove there and found a good spot about 3km away from the transmitter.
And that one gave the best results. I report measurements for all 3 masts. Maybe it will give some more information what to do with the project.

## GSM Mast A:

 ** Peak signal in mast direction: **
 
   936.568 MHz     6.26 dB                                                                                                                                                                                                          
   953.135 MHz     5.58 dB                                                                                                                                                                                                          
   936.504 MHz     5.21 dB                                                                                                                                                                                                          
   953.108 MHz     5.16 dB                                                                                                                                                                                                          
   936.541 MHz     5.02 dB                                                                                                                                                                                                          
   953.070 MHz     4.40 dB                                                                                                                                                                                                          
   936.563 MHz     4.32 dB                                                                                                                                                                                                          
   953.130 MHz     4.22 dB                                                                                                                                                                                                          
   936.520 MHz     4.19 dB                                                                                                                                                                                                          
   936.574 MHz     3.99 dB
   
   ```
   f_ref_hz            = 936.568 MHz
   bearing_to_tower_deg = 144
   distance_to_tower_m  = 650
   RSSI per heading:
   RSSI per heading (deg : dBFS):
    0    -0.17
   45    -0.30
   90    -1.23
  135     0.69
  180     0.73
  225     0.73
  270     0.51
  315    -0.15
   ```
   
   ** verdict: FAIL **
   
   Attach: /docs/phase-c-report/phase_c_polar_A.png

## GSM Mast B:

 ** Peak signal in mast direction: **
 
 935.968 MHz     3.08 dB
 935.941 MHz     2.90 dB
 958.695 MHz     2.42 dB
 935.903 MHz     2.19 dB
 937.569 MHz     2.15 dB
 953.535 MHz     1.90 dB
 958.684 MHz     1.46 dB
 958.722 MHz     1.43 dB
 958.728 MHz     1.28 dB
 953.508 MHz     1.25 dB
 
 ```
 f_ref_hz            = 935.968 MHz
 bearing_to_tower_deg = 336
 distance_to_tower_m  = 2200
 RSSI per heading (deg : dBFS):
 0     0.29
 45     0.78
 90     0.34
 135    -6.14
 180    -7.72
 225    -6.78
 270    -2.15
 315    -1.23


   ```
   
   ** verdict: FAIL **
   
   Attach: /docs/phase-c-report/phase_c_polar_B.png

## GSM Mast C:

 ** Peak signel in mast direction: **
 
 958.695 MHz     8.28 dB
 958.728 MHz     7.80 dB
 958.722 MHz     7.74 dB
 958.684 MHz     6.88 dB
 958.657 MHz     6.31 dB
 958.701 MHz     6.29 dB
 958.636 MHz     6.27 dB
 958.706 MHz     6.10 dB
 925.346 MHz     5.97 dB
 958.749 MHz     5.96 dB

 ```
 f_ref_hz            = 936.968 MHz
 bearing_to_tower_deg = 306
 distance_to_tower_m  = 3000
 RSSI per heading (deg : dBFS):
 0    -4.79
 45    -9.43
 90   -14.97
 135    -9.50
 180   -10.29
 225    -8.63
 270    -0.60
 315    -0.07

 ```
   
 ** verdict: SUCCESS (is it?) **
 
 Attach: /docs/phase-c-report/phase_c_polar_C.png
