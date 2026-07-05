"""
pipeline/
=========
Data-processing and emulation-profile generation modules.

    nasa_power  — download and parsing of the NASA POWER API
    profile     — V_set/I_set/P_set setpoint computation + strategies
    seqlog      — export to the EA Power Control SeqLog CSV format
"""
from pipeline import nasa_power, profile, seqlog

__all__ = ["nasa_power", "profile", "seqlog"]
