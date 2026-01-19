import importlib.util
import inspect
import os
import sys
from typing import Dict, Type, List, Optional

from simaas.core.logging import Logging
from simaas.dor.api import DORRESTService
from simaas.rti.api import RTIRESTService

logger = Logging.get('plugins')


def discover_plugins(plugin_paths: List[str]) -> Dict[str, Dict[str, Type]]:
    """Discover plugins from given paths."""
    registry = {'dor': {}, 'rti': {}}

    for path in plugin_paths:
        if not os.path.isdir(path):
            logger.warning(f"plugin path not found: {path}")
            continue

        _scan_directory(path, registry)

    logger.info(f"discovered {len(registry['dor'])} DOR plugins: {list(registry['dor'].keys())}")
    logger.info(f"discovered {len(registry['rti'])} RTI plugins: {list(registry['rti'].keys())}")

    return registry


def _scan_directory(path: str, registry: Dict[str, Dict[str, Type]]) -> None:
    """Scan directory for plugin packages."""
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if not os.path.isdir(entry_path):
            continue

        init_file = os.path.join(entry_path, '__init__.py')
        if not os.path.exists(init_file):
            continue

        _load_plugin_module(entry_path, entry, registry)


def _load_plugin_module(plugin_path: str, plugin_dir: str, registry: Dict[str, Dict[str, Type]]) -> None:
    """Load a plugin module and extract plugin classes."""
    try:
        module_name = f"_plugin_{plugin_dir}"
        spec = importlib.util.spec_from_file_location(module_name, os.path.join(plugin_path, '__init__.py'))
        if spec is None or spec.loader is None:
            logger.warning(f"failed to load plugin from {plugin_path}")
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        _extract_plugin_classes(module, registry)

    except Exception as e:
        logger.warning(f"error loading plugin from {plugin_path}: {e}")


def _extract_plugin_classes(module, registry: Dict[str, Dict[str, Type]]) -> None:
    """Extract plugin classes from module."""
    for name, obj in inspect.getmembers(module, inspect.isclass):
        try:
            if issubclass(obj, DORRESTService) and obj is not DORRESTService:
                if hasattr(obj, 'plugin_name'):
                    plugin_name = obj.plugin_name()
                    if plugin_name in registry['dor']:
                        logger.warning(f"DOR plugin '{plugin_name}' already registered, overwriting")
                    registry['dor'][plugin_name] = obj

            elif issubclass(obj, RTIRESTService) and obj is not RTIRESTService:
                if hasattr(obj, 'plugin_name'):
                    plugin_name = obj.plugin_name()
                    if plugin_name in registry['rti']:
                        logger.warning(f"RTI plugin '{plugin_name}' already registered, overwriting")
                    registry['rti'][plugin_name] = obj

        except TypeError:
            pass


def get_plugin_class(registry: Dict[str, Dict[str, Type]], category: str, name: str) -> Optional[Type]:
    """Get plugin class by category and name."""
    if name == 'none':
        return None

    if category not in registry:
        return None

    return registry[category].get(name)
