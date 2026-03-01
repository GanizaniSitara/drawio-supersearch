"""
Configuration for the diagram conversion pipeline.
"""

import os
import configparser
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ConversionConfig:
    """Configuration for the diagram conversion pipeline."""

    # Input/output directories
    screenshots_dir: str = "./data/content/images"
    metadata_dir: str = "./data/content/metadata"
    output_dir: str = "./data/converted"
    drawio_output_dir: str = ""  # derived
    c4_output_dir: str = ""  # derived
    db_path: str = "./data/converted/conversion.db"

    # API settings
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    vision_model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.0

    # Batch processing
    batch_size: int = 10
    max_concurrent: int = 3
    retry_count: int = 2
    retry_delay: float = 2.0

    # Quality thresholds
    min_confidence_score: float = 0.5
    auto_accept_threshold: float = 0.8
    review_threshold: float = 0.5

    # Server settings
    server_host: str = "127.0.0.1"
    server_port: int = 8000

    # Confluence linkback
    confluence_url: str = ""

    def __post_init__(self):
        if not self.drawio_output_dir:
            self.drawio_output_dir = os.path.join(self.output_dir, "drawio")
        if not self.c4_output_dir:
            self.c4_output_dir = os.path.join(self.output_dir, "c4")
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def ensure_directories(self):
        """Create all required output directories."""
        for d in [
            self.output_dir,
            self.drawio_output_dir,
            self.c4_output_dir,
            os.path.dirname(self.db_path),
        ]:
            os.makedirs(d, exist_ok=True)

    @classmethod
    def from_ini(cls, ini_path: str) -> "ConversionConfig":
        """Load configuration from an INI file."""
        config = configparser.ConfigParser()
        config.read(ini_path)
        base_dir = os.path.dirname(os.path.abspath(ini_path))

        def resolve(path: str) -> str:
            if path.startswith("./") or path.startswith("../"):
                return os.path.normpath(os.path.join(base_dir, path))
            return path

        kwargs = {}

        if config.has_section("Conversion"):
            section = config["Conversion"]
            if "screenshots_dir" in section:
                kwargs["screenshots_dir"] = resolve(section["screenshots_dir"])
            if "metadata_dir" in section:
                kwargs["metadata_dir"] = resolve(section["metadata_dir"])
            if "output_dir" in section:
                kwargs["output_dir"] = resolve(section["output_dir"])
            if "db_path" in section:
                kwargs["db_path"] = resolve(section["db_path"])

        if config.has_section("API"):
            section = config["API"]
            if "anthropic_api_key" in section:
                kwargs["anthropic_api_key"] = section["anthropic_api_key"]
            if "model" in section:
                kwargs["model"] = section["model"]
            if "vision_model" in section:
                kwargs["vision_model"] = section["vision_model"]
            if "max_tokens" in section:
                kwargs["max_tokens"] = int(section["max_tokens"])

        if config.has_section("Batch"):
            section = config["Batch"]
            if "batch_size" in section:
                kwargs["batch_size"] = int(section["batch_size"])
            if "max_concurrent" in section:
                kwargs["max_concurrent"] = int(section["max_concurrent"])

        if config.has_section("Quality"):
            section = config["Quality"]
            if "min_confidence_score" in section:
                kwargs["min_confidence_score"] = float(section["min_confidence_score"])
            if "auto_accept_threshold" in section:
                kwargs["auto_accept_threshold"] = float(section["auto_accept_threshold"])

        if config.has_section("Server"):
            section = config["Server"]
            if "host" in section:
                kwargs["server_host"] = section["host"]
            if "port" in section:
                kwargs["server_port"] = int(section["port"])

        if config.has_section("Confluence"):
            section = config["Confluence"]
            if "url" in section:
                kwargs["confluence_url"] = section["url"]

        return cls(**kwargs)
