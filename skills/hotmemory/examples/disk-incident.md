# 2026-10-02 Post-mortem: the disk filled at noon

## Summary

The disk of the log host filled at noon. Writes failed for ten minutes.

## Root cause

The log rotation ran at midnight only. The noon batch doubled the log volume.

## Appendix

```sh
# show the disk use
df -h /var/log
```
