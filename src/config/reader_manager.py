# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Reader Configuration Management System
# =============================================================================
#
# This module manages satellite reader configurations from YAML files
# and provides dynamic reader selection based on file characteristics.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
# =============================================================================

import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class ReaderManager:
    """Manages satellite reader configurations and provides dynamic reader selection."""
    
    def __init__(self, config_dir: Optional[str] = None):
        """Initialize the reader manager.
        
        Args:
            config_dir: Directory containing reader configuration YAML files.
                       If None, uses default src/config/readers directory.
        """
        if config_dir is None:
            self.config_dir = Path(__file__).parent / "readers"
        else:
            self.config_dir = Path(config_dir)
            
        self.reader_configs: Dict[str, Dict[str, Any]] = {}
        self._load_reader_configurations()
        
        # Built-in default configurations for common readers
        self.builtin_defaults = {
            "ahi_hsd": {
                "group_keys": ["start_time"],
                "reader_kwargs": {}
            },
            "abi_l1b": {
                "group_keys": ["start_time", "platform_shortname"],
                "reader_kwargs": {}
            },
            "seviri_l1b_native": {
                "group_keys": ["end_time", "satid", "instr"],
                "reader_kwargs": {"fill_disk": True}
            },
            "modis_l1b": {
                "group_keys": ["start_time"],
                "reader_kwargs": {}
            }
        }
    
    def _load_reader_configurations(self):
        """Load all reader configuration YAML files from the config directory."""
        if not self.config_dir.exists():
            logger.warning(f"Reader config directory not found: {self.config_dir}")
            return
            
        config_files = list(self.config_dir.glob("*.yaml")) + list(self.config_dir.glob("*.yml"))
        
        for config_file in config_files:
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f)
                    
                if config_data and isinstance(config_data, dict):
                    # Extract reader name from the data_reading section
                    if "data_reading" in config_data:
                        for reader_name, reader_config in config_data["data_reading"].items():
                            self.reader_configs[reader_name] = reader_config
                            logger.debug(f"Loaded reader configuration: {reader_name}")
                    else:
                        # Fallback: assume the top-level key is the reader name
                        for reader_name, reader_config in config_data.items():
                            if isinstance(reader_config, dict):
                                self.reader_configs[reader_name] = reader_config
                                logger.debug(f"Loaded reader configuration: {reader_name}")
                                
            except Exception as e:
                logger.error(f"Failed to load reader configuration from {config_file}: {e}")
    
    def get_reader_config(self, reader_name: str) -> Dict[str, Any]:
        """Get configuration for a specific reader.
        
        Args:
            reader_name: Name of the reader (e.g., 'ahi_hsd', 'abi_l1b')
            
        Returns:
            Dictionary containing reader configuration. Returns built-in default
            if no file-based configuration is found.
        """
        # First check if we have a file-based configuration
        if reader_name in self.reader_configs:
            return self.reader_configs[reader_name].copy()
        
        # Fall back to built-in defaults
        if reader_name in self.builtin_defaults:
            logger.debug(f"Using built-in default configuration for {reader_name}")
            return self.builtin_defaults[reader_name].copy()
        
        # Return empty configuration if nothing is found
        logger.warning(f"No configuration found for reader {reader_name}, using empty config")
        return {}
    
    def get_appropriate_reader(self, filenames: List[str]) -> str:
        """Determine the appropriate reader for given filenames.
        
        Args:
            filenames: List of file paths to analyze
            
        Returns:
            Reader name that should be used for the files
        """
        if not filenames:
            return "ahi_hsd"  # Default fallback
            
        # Try to use satpy's built-in reader detection if available
        try:
            from satpy.readers import find_files_and_readers
            matches = find_files_and_readers(filenames=filenames)
            if matches:
                # Pick the reader that matched the most files (or the first)
                best_reader = max(matches.keys(), key=lambda r: len(matches[r]))
                logger.debug(f"Satpy detected reader: {best_reader}")
                return best_reader
        except ImportError:
            logger.warning("Satpy not available for reader detection")
        except Exception:
            # Satpy couldn't read the file with default detection
            pass
        
        # Fall back to file extension and header analysis
        first_file = Path(filenames[0])
        
        # Check file extension
        if first_file.suffix.lower() in ['.nc', '.netcdf']:
            # NetCDF files - need to determine specific type
            return self._determine_netcdf_reader(filenames)
        elif first_file.suffix.lower() in ['.hdf', '.hdf4', '.hdf5', '.h5']:
            # HDF files
            return self._determine_hdf_reader(filenames)
        elif first_file.suffix.lower() in ['.nat']:
            # SEVIRI native files
            return self._determine_seviri_reader(filenames)
        elif first_file.suffix.lower() in ['.dat', '.DAT']:
            # DAT files - likely HSD format
            return "ahi_hsd"
        elif first_file.suffix.lower() in ['.hdr', '.HDR']:
            # HDR files
            return "ahi_hsd"
        
        # Default fallback
        return "ahi_hsd"
    
    def _determine_netcdf_reader(self, filenames: List[str]) -> str:
        """Determine the appropriate NetCDF-based reader.
        
        Args:
            filenames: List of NetCDF file paths
            
        Returns:
            Reader name for NetCDF files
        """
        # Check if we have access to the files to read attributes
        try:
            import xarray as xr
            # Try to read the first file to check its attributes
            with xr.open_dataset(filenames[0]) as ds:
                # Check for GOES ABI characteristics
                if 'platform_id' in ds.attrs:
                    platform_id = str(ds.attrs['platform_id']).upper()
                    if 'GOES' in platform_id or 'G' in platform_id:
                        return "abi_l1b"
                
                # Check for Himawari characteristics
                if 'satellite_id' in ds.attrs:
                    satellite_id = str(ds.attrs['satellite_id']).upper()
                    if 'HIMARAWI' in satellite_id or 'HIM' in satellite_id:
                        return "ahi_hsd"
                        
                # Check for SEVIRI characteristics
                if 'spatial_coverage' in ds.attrs:
                    spatial_coverage = str(ds.attrs['spatial_coverage']).upper()
                    if 'SEVIRI' in spatial_coverage or 'MSG' in spatial_coverage:
                        return "seviri_l1b_native"
                        
        except Exception as e:
            logger.debug(f"Could not read NetCDF attributes for reader determination: {e}")
        
        # Default fallback for NetCDF files
        return "abi_l1b"  # Common default for weather satellite NetCDF
    
    def _determine_seviri_reader(self, filenames: List[str]) -> str:
        """Determine the reader for SEVIRI native (.nat) files.

        Args:
            filenames: List of SEVIRI native file paths

        Returns:
            Reader name for SEVIRI files
        """
        first_file = Path(filenames[0]).name.upper()
        if 'SEVI' in first_file or 'MSG' in first_file:
            return "seviri_l1b_native"
        return "seviri_l1b_native"

    def _determine_hdf_reader(self, filenames: List[str]) -> str:
        """Determine the appropriate HDF-based reader.
        
        Args:
            filenames: List of HDF file paths
            
        Returns:
            Reader name for HDF files
        """
        # Check file characteristics to determine HDF reader type
        # This would typically involve reading HDF attributes
        # For now, return a reasonable default
        
        first_file = Path(filenames[0]).name.upper()
        if 'MODIS' in first_file:
            return "modis_l1b"
        elif 'VIIRS' in first_file:
            return "viirs_l1b"
        elif first_file.startswith(('SVM', 'SVI', 'SVN', 'SVD', 'GMODO', 'GITCO', 'IVM', 'GPS')):
            # VIIRS SDR / RDR naming convention (SVM01, SVI02, ...) plus VIIRS geo files
            return "viirs_l1b"
        elif 'NPP' in first_file or 'JPSS' in first_file or 'NOAA20' in first_file:
            return "viirs_l1b"
        else:
            # Default fallback for unknown HDF files
            return "modis_l1b"


# Global instance for easy access
reader_manager = ReaderManager()