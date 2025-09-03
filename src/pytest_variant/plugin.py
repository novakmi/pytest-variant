"""
pytest-variant plugin: Provides parametrization and fixtures for testing multiple product variants.

This plugin allows you to specify variants (e.g., product versions, configurations) via
command-line options or ini files.
Variants can have multiple attributes and a variant name. The plugin provides fixtures and helpers to access
variants, their attributes, and to parametrize tests accordingly.

Key features:
- Parse and deduplicate variant attributes from command line or ini
- Parametrize tests for all specified variants
- Provide fixtures for variant attributes and variant-specific test logic
- Support for variant setup/discovery strings
"""

import logging
from typing import List, Optional

import pytest

log = logging.getLogger(__name__)


def pytest_addoption(parser):
    """
    Add command-line options and ini options for variant and variant-setup.
    """
    group = parser.getgroup('variant')
    group.addoption(
        '--variant',
        action='append',  # allow multiple --variant arguments
        dest='variant',
        default=None,
        help='Variant specification(s). Can be repeated.'
             ' Format: <attr1>:<attr2>:...:<variant>[,<...>].'
             ' Colons and commas can be escaped with \\.'
    )
    group.addoption(
        '--variant-setup',
        action='store',
        dest='variant_setup',
        default=None,
        help='General setup string for variant discovery (e.g. directory, '
             'server location). Same syntax as --variant, '
             'but cannot be repeated.'
    )
    parser.addini('VARIANTS',
                  'Default variants (comma-separated, '
                  'same format as --variant)')
    parser.addini('VARIANT_SETUP', 'Default variant-setup string')


def _split_escaped(s, sep):
    """
    Split a string by sep, but allow escaping sep with backslash only if it precedes sep.
    Only split on unescaped sep. Remove escape backslashes from result.
    """
    log.debug("==> _split_escaped s=%s, sep=%s", s, sep)
    parts = []
    buf = ''
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s) and s[i + 1] == sep:
            buf += sep
            i += 2
        elif s[i] == sep:
            parts.append(buf)
            buf = ''
            i += 1
        else:
            buf += s[i]
            i += 1
    parts.append(buf)
    log.debug("<== _split_escaped ret=%s", parts)
    return parts


def _parse_variant_args_to_lists(variant_args: Optional[List[str]]) -> List[List[str]]:
    """
    Convert variant argument strings into structured attribute lists.

    Takes raw variant specification strings and converts them into a list of attribute lists,
    where each inner list contains attributes followed by the variant name as the last element.

    Args:
        variant_args: List of variant specification strings from command-line or config.
                     Each string uses colon (:) to separate attributes and comma (,) to
                     separate different variants within the same argument.

    Returns:
        List of attribute lists where each inner list has format:
        [attribute1, attribute2, ..., variant_name]

    Behavior:
        - Splits on commas (,) to separate variants within each argument string
        - Splits on colons (:) to separate attributes from variant names
        - Supports attribute inheritance: variants without explicit attributes
          inherit from the previous variant in the same argument string
        - Resets inheritance context across different variant_args elements
        - Filters out empty segments automatically
        - Respects escaped separators (\\: and \\,)

    Examples:
        >>> _parse_variant_args_to_lists(["prod:web:v1,mobile:v2"])
        [['prod', 'web', 'v1'], ['mobile', 'v2']]

        >>> _parse_variant_args_to_lists(["prod:v1,v2"])  # v2 inherits 'prod'
        [['prod', 'v1'], ['prod', 'v2']]

        >>> _parse_variant_args_to_lists(["prod:v1", "test:v2"])  # inheritance resets
        [['prod', 'v1'], ['test', 'v2']]
    """
    log.debug("==> _parse_variant_args_to_lists variant_args=%s", variant_args)
    ret = []
    if not variant_args:
        log.debug("<== _parse_variant_args_to_lists ret=%s", ret)
        return ret
    for arg in variant_args:
        prev_attrs = []
        for vstr in _split_escaped(arg, ','):
            attrs = _split_escaped(vstr, ':')
            attrs = [a for a in attrs if a]
            if len(attrs) == 1 and prev_attrs:
                attrs = prev_attrs + [attrs[0]]
            if len(attrs) > 1:
                prev_attrs = attrs[:-1]
            if attrs:
                ret.append(attrs)
    log.debug("<== _parse_variant_args_to_lists ret=%s", ret)
    return ret


class VariantPluginBase:
    """
    Abstract base class for variant-oriented pytest plugins.
    Each instance represents a single variant, with attributes (unique, sorted) and a variant name.
    Provides helpers for parsing, deduplication, and variant/attribute access.
    """

    def __init__(self, variant: str, attributes: List[str] = None):
        """
        Initialize a VariantPluginBase object.
        :param variant: The variant name (string).
        :param attributes: List of attribute strings (excluding the variant name). Defaults to empty list if None.
        """
        log.debug("==> VariantPluginBase.__init__ variant=%s, attributes=%s",
                  variant, attributes)
        # Store attributes as a sorted set (unique, order not preserved)
        self.attributes = sorted(set(attributes or []))
        self.variant = variant  # variant name (string)
        log.debug("<== VariantPluginBase.__init__ ret=None")

    @property
    def attrs(self):
        """
        Backward-compatible property for attributes.
        """
        return self.attributes

    @classmethod
    def parse_variants(cls, variant_args: Optional[List[str]]) -> List["VariantPluginBase"]:
        """
        Parse variant argument strings and return a list of VariantPluginBase objects.

        Primary entry point for converting command-line or configuration variant specifications
        into structured variant objects with deduplication and attribute merging.

        Args:
            variant_args: List of variant specification strings from --variant args or ini config.
                         Format: "attr1:attr2:variant,attr3:variant2". Can be None or empty.

        Returns:
            List of VariantPluginBase objects, one per unique variant name. Duplicate variants
            have their attributes merged.

        Examples:
            >>> # Simple variants
            >>> objs = VariantPluginBase.parse_variants(["v1,v2"])
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [([], 'v1'), ([], 'v2')]

            >>> # With attributes
            >>> objs = VariantPluginBase.parse_variants(["prod:web:v1,mobile:v2"])
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [(['prod', 'web'], 'v1'), (['mobile'], 'v2')]

            >>> # Attribute inheritance and merging
            >>> objs = VariantPluginBase.parse_variants(["prod:v1,v2", "test:v1"])
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [(['prod', 'test'], 'v1'), (['prod'], 'v2')]

        Notes:
            - Uses colon (:) for attributes, comma (,) for variants
            - Supports escaping with backslash (\\: and \\,)
            - Attributes inherit within same argument, reset across arguments
            - Delegates to parse_variants_from_list() for object creation
        """
        log.debug("==> VariantPluginBase.parse_variants variant_args=%s",
                  variant_args)
        attr_lists = _parse_variant_args_to_lists(variant_args)
        ret = cls.parse_variants_from_list(attr_lists)
        log.debug("<== VariantPluginBase.parse_variants ret=%s", ret)
        return ret

    @classmethod
    def parse_variants_from_list(cls, attr_lists: List[List[str]]) -> List["VariantPluginBase"]:
        """
        Create VariantPluginBase objects from pre-parsed attribute lists with deduplication.

        Core method that handles variant object creation and attribute merging. Takes attribute
        lists (last element = variant name) and creates objects, deduplicating by variant name.

        Args:
            attr_lists: List of attribute lists in format [attr1, attr2, ..., variant_name].
                       Last element is variant name, preceding elements are attributes.
                       Empty lists are ignored.

        Returns:
            List of VariantPluginBase objects, one per unique variant name. Duplicate variants
            have their attributes merged (set union).

        Examples:
            >>> # Different variants
            >>> attr_lists = [['prod', 'web', 'v1'], ['test', 'v2']]
            >>> objs = VariantPluginBase.parse_variants_from_list(attr_lists)
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [(['prod', 'web'], 'v1'), (['test'], 'v2')]

            >>> # Deduplication - same variant name
            >>> attr_lists = [['prod', 'v1'], ['test', 'v1']]
            >>> objs = VariantPluginBase.parse_variants_from_list(attr_lists)
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [(['prod', 'test'], 'v1')]  # Attributes merged

            >>> # No attributes
            >>> attr_lists = [['v1'], ['v2']]
            >>> objs = VariantPluginBase.parse_variants_from_list(attr_lists)
            >>> [(obj.attributes, obj.variant) for obj in objs]
            [([], 'v1'), ([], 'v2')]

        Notes:
            - Used internally by parse_variants() after string parsing
            - Attributes are sorted for consistent ordering
            - Variant names are case-sensitive
            - Empty attr_lists are ignored
        """
        log.debug(
            "==> VariantPluginBase.parse_variants_from_list attr_lists=%s",
            attr_lists)
        variant_map = {}
        for attrs in attr_lists:
            if not attrs:
                continue
            *attributes, variant = attrs
            if variant in variant_map:
                variant_map[variant].update(attributes)
            else:
                variant_map[variant] = set(attributes)
        ret = [cls(variant=variant, attributes=sorted(attributes)) for
               variant, attributes in variant_map.items()]
        log.debug("<== VariantPluginBase.parse_variants_from_list ret=%s", ret)
        return ret

    @staticmethod
    def get_attributes(variant_objs: list) -> list:
        """
        Return a sorted list of all unique attributes from all VariantPluginBase objects.
        """
        log.debug("==> VariantPluginBase.get_attributes variant_objs=%s",
                  variant_objs)
        attributes = set()
        for obj in variant_objs:
            attributes.update(obj.attributes)
        ret = sorted(attributes)
        log.debug("<== VariantPluginBase.get_attributes ret=%s", ret)
        return ret

    @staticmethod
    def get_variants(variant_objs: list, attributes=None) -> list:
        """
        Return a list of VariantPluginBase objects matching the specified attributes.

        :param variant_objs: List of VariantPluginBase objects.
        :param attributes: Single attribute (string), list of attributes, or None for variants with no attributes.
        :return: List of VariantPluginBase objects.
        """
        log.debug(
            "==> VariantPluginBase.get_variants variant_objs=%s, attributes=%s",
            variant_objs, attributes)

        # Normalize attributes to a list for consistent processing
        if attributes is None:
            attr_list = None
        elif isinstance(attributes, str):
            attr_list = [attributes]
        else:
            attr_list = attributes

        # Filter variant objects
        if attr_list is None or not attr_list:
            filtered_variants = [obj for obj in variant_objs if not obj.attributes]
        else:
            filtered_variants = [obj for obj in variant_objs
                                 if any(attr in obj.attributes for attr in attr_list)]

        ret = sorted(filtered_variants, key=lambda x: x.variant)
        log.debug("<== VariantPluginBase.get_variants ret=%s",
                  [(obj.attributes, obj.variant) for obj in ret])
        return ret


def get_all_variant_objs(config):
    """
    Read config and parse all variant objects for the current session.
    Returns a list of VariantPluginBase objects.
    """
    log.debug("==> get_all_variant_objs config=%s", config)
    variant_args = config.getoption('variant')
    if not variant_args:
        ini_variants = config.getini('VARIANTS')
        variant_args = [ini_variants] if ini_variants else []
    ret = VariantPluginBase.parse_variants(variant_args)
    log.debug("<== get_all_variant_objs ret=%s", ret)
    return ret


def pytest_generate_tests(metafunc):
    """
    Parametrize tests with all variants if the 'variant' fixture is used.
    """
    log.debug("==> pytest_generate_tests metafunc=%s", metafunc)
    variant_objs = get_all_variant_objs(metafunc.config)
    if 'variant' in metafunc.fixturenames:
        ids = [":".join(obj.attributes + [obj.variant]) for obj in variant_objs]
        metafunc.parametrize('variant', variant_objs, ids=ids)
        log.debug("<== pytest_generate_tests ret=None (parametrized)")
    else:
        log.debug("<== pytest_generate_tests ret=None (not parametrized)")


@pytest.fixture
def variant(request):
    """
    Fixture providing the current variant object to tests.
    """
    log.debug("==> variant request=%s", request)
    ret = request.param if hasattr(request, 'param') else None
    log.debug("<== variant ret=%s", ret)
    return ret


@pytest.fixture
def variant_setup(request):
    """
    Fixture providing the variant-setup as a list of VariantPluginBase objects (for setup/discovery).
    """
    log.debug("==> variant_setup request=%s", request)
    config = request.config
    setup_str = config.getoption('variant_setup') or config.getini(
        'VARIANT_SETUP')
    if not setup_str:
        log.debug("<== variant_setup ret=[] (no setup_str)")
        return []
    ret = VariantPluginBase.parse_variants([setup_str])
    log.debug("<== variant_setup ret=%s", ret)
    return ret


@pytest.fixture
def variant_filter(request):
    """
    Returns a function with methods for different filtering needs for variants:
    - variant_filter.by_attribute(attr) -> variant objects with single attribute
    - variant_filter.by_attributes(attrs) -> variant objects with any of the attributes
    - variant_filter.all_variants() -> all variant objects
    - variant_filter.all_variant_attributes() -> all unique attributes from all variants
    """
    log.debug("==> variant_filter request=%s", request)
    variant_objs = get_all_variant_objs(request.config)

    class VariantFilter:

        def by_attribute(self, attribute=None):
            """Get variant objects for single attribute - returns full objects instead of just names"""
            return VariantPluginBase.get_variants(variant_objs, attribute)

        def by_attributes(self, attributes=None):
            """Get variant objects with any of the attributes (replaces variants_with_attributes)"""
            return VariantPluginBase.get_variants(variant_objs, attributes)

        def all_variants(self):
            """Get all variant objects"""
            return variant_objs

        def all_variant_attributes(self):
            """Get all unique attributes from all variants"""
            return VariantPluginBase.get_attributes(variant_objs)

    log.debug("<== variant_filter ret=VariantFilter")
    return VariantFilter()


def pytest_report_header(config):
    """
    Add a header to the pytest report showing the variants and setup string.
    """
    log.debug("==> pytest_report_header config=%s", config)
    variant_args = config.getoption('variant')
    if not variant_args:
        variant_args = [config.getini('VARIANTS')] if config.getini(
            'VARIANTS') else []
    variant_setup = config.getoption('variant_setup') or config.getini(
        'VARIANT_SETUP')
    ret = f"Variants: {variant_args} | Variant-setup: {variant_setup}"
    log.debug("<== pytest_report_header ret=%s", ret)
    return ret
