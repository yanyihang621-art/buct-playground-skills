# Bench sensor evaluation

## Reference subtraction

After matching timestamps, we subtracted the reference trace from each paired sensor trace and smoothed the residuals.

## Experimental setup

We fitted the offset using calibration sensors, selected the window on validation sensors, and evaluated the held-out sensors once.

## Parameters

The smoother used a window of 5 samples.

## Dataset and evaluation

We partitioned release A of BenchTrace by sensor.

## Controls

On the same sensor partition, we compared the smoothed residual with the raw residual using the median absolute error across sensors.

## Reproducibility and limitations

The bench logger supplied the measurements for three repeats in fixed order. The log contains no outdoor measurements.
