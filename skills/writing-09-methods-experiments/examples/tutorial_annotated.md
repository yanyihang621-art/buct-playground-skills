# Bench sensor evaluation

## Reference subtraction

After matching timestamps, we subtracted the reference trace from each paired sensor trace and smoothed the residuals.⟦Eea4a86357438,E38c1cc26a525,Ed1f2fed6a2e8,Eb56aab7506c4⟧

## Experimental setup

We fitted the offset using calibration sensors, selected the window on validation sensors, and evaluated the held-out sensors once.⟦E2ca6136d8c0d,E9529574c6ad9,E05f5c4e00ef1⟧

## Parameters

The smoother used a window of 5 samples.⟦E5356246945a0,E5f0db09b32f2,Ef5ddb4763070⟧

## Dataset and evaluation

We partitioned release A of BenchTrace by sensor.⟦E120f333dfd06,Ea1c41f268c99,E650dbcd407ce⟧

## Controls

On the same sensor partition, we compared the smoothed residual with the raw residual using the median absolute error across sensors.⟦E1a605e04f2ce,Ee2b011d891f3,E119c112032c3,E1d8d12266b35⟧

## Reproducibility and limitations

The bench logger supplied the measurements for three repeats in fixed order. The log contains no outdoor measurements.⟦Eb3c328b2e4c6,E9dd346668fcb,E3d5b260445c7,E34de31fb4795⟧
