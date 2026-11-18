"""The existing 65,538-object restore ceiling applies before extraction."""
from pathlib import PurePosixPath
import tarfile

import pytest

from test_archive_hardening import ArchiveVerifier
from test_bash_archive_boundaries import source_revision

# Use the same source-revision fixture as the other real TAR regression tests.
source_revision = source_revision


def expanded_paths(names):
    paths = set()
    for name in names:
        path = PurePosixPath(name)
        paths.add(str(path))
        paths.update(str(parent) for parent in path.parents if str(parent) != '.')
    return paths


def check_archive(tmp_path, names):
    verifier = ArchiveVerifier(tmp_path)
    with tarfile.open(verifier.path, 'w:gz') as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            if name.endswith('/'):
                member.type = tarfile.DIRTYPE
            archive.addfile(member)
    verifier.checksum()
    return verifier.run()


@pytest.mark.parametrize('shape', ['deep', 'directory-and-punctuation-siblings'])
@pytest.mark.parametrize('footprint,accepted', [(65537, True), (65538, True), (65539, False)])
def test_implicit_parents_obey_the_existing_stage_boundary_before_extraction(tmp_path, footprint, accepted, shape):
    # Real long-name TAR records create many parents from only ~520 members.
    names = ['var/named/tree%03d/' % number + 'd/' * 167 + 'zone' for number in range(387)]
    if shape == 'directory-and-punctuation-siblings':
        # A listed parent and its children must stay one object even when !
        # siblings sort before '/' in the native byte ordering.
        names += ['var/named/prefix/', 'var/named/prefix!/zone',
                  'var/named/prefix/zone', 'var/named/prefixa/zone']
    existing = len(expanded_paths(names))
    names += ['var/named/extra%03d' % number for number in range(footprint - existing)]
    assert len(names) < 65536 and len(expanded_paths(names)) == footprint
    result = check_archive(tmp_path, names)
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert 'footprint' in result.stderr.lower()


def test_shared_implicit_parents_are_counted_once(tmp_path):
    names = ['var/named/shared/' + 'd/' * 100 + 'zone%03d' % number for number in range(700)]
    assert len(expanded_paths(names)) == 803
    result = check_archive(tmp_path, names)
    assert result.returncode == 0, result.stderr



@pytest.mark.parametrize('name,accepted', [
    ('var/named/' + 'd/' * 2043, True),
    ('var/named/' + 'd/' * 2043 + 'x', False),
    ('var/named/' + 'd/' * 2042 + 'dd/', False),
])
def test_raw_tar_path_length_is_checked_before_directory_suffix_normalization(tmp_path, name, accepted):
    # Normalizing a trailing slash must never admit a 4,097-byte directory.
    assert len(name) == (4096 if accepted else 4097)
    result = check_archive(tmp_path, [name])
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert '4096 bytes' in result.stderr


@pytest.mark.parametrize('names', [
    ['var/named/prefix/', 'var/named/prefix!/zone',
     'var/named/prefix/zone', 'var/named/prefixa/zone'],
    ['etc/named.conf', 'etc/named/tree/zone', 'var/named/zone'],
    ['var/named/quote[?]/z', 'var/named/quote[?]/zone',
     'var/named/quote[?]a/zone', 'var/named/shared/last/'],
])
def test_literal_component_boundaries_survive_lexical_archive_order(tmp_path, names):
    result = check_archive(tmp_path, list(reversed(names)))
    assert result.returncode == 0, result.stderr


def test_parent_keys_are_literal_data_even_on_legacy_bash(tmp_path):
    marker = tmp_path / 'must-not-exist'
    # Avoid an absolute slash embedded in a component and keep real canonical
    # paths. Substitution-like bytes must remain data, never shell input.
    names = ['var/named/$(touch${IFS}' + str(marker).replace('/', '${PATH:0:1}') + ')/zone',
             'var/named/quote"single\'bracket[0]/zone', 'var/named/space and tab-free/zone']
    result = check_archive(tmp_path, names)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
