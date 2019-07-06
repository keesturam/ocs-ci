import logging
import pytest

from ocs_ci.utility import templating
from ocs_ci.utility.utils import run_cmd
from ocs_ci.framework.testlib import tier3, ManageTest
from ocs_ci.ocs import defaults, constants
from ocs_ci.ocs.resources import pod
from tests import helpers


log = logging.getLogger(__name__)

pod_obj = None
pvc_obj = None


@pytest.fixture(scope='class')
def test_fixture(request):
    """
    Test fixture
    """
    self = request.node.cls

    def finalizer():
        teardown(self)
    request.addfinalizer(finalizer)
    setup(self)


def teardown(self):
    """
    Deletes POD, PVC, Storageclass and Secret after the test
    """
    if hasattr(pod_obj, "get"):
        pod_obj.delete()
        pod_obj.ocp.wait_for_delete(resource_name=pod_obj.name)
        log.info(f"{pod_obj.name} is deleted")
    if hasattr(pvc_obj, "get"):
        pvc_obj.delete()
        pvc_obj.ocp.wait_for_delete(resource_name=pvc_obj.name)
        log.info(f"{pvc_obj.name} is deleted")
    self.sc_obj.delete()
    self.sec_obj.delete()


def setup(self):
    """
    Create secret and Storage class for the test
    """
    self.sec_obj = helpers.create_secret(constants.CEPHBLOCKPOOL)
    self.sc_obj = helpers.create_storage_class(
        interface_type=constants.CEPHBLOCKPOOL,
        secret_name=self.sec_obj.name,
        interface_name=constants.DEFAULT_BLOCKPOOL,
        sc_name=helpers.create_unique_resource_name('test', 'storageclass')
    )


def ceph_storage_capacity():
    """
    Returns the total capacity of the ceph cluster
    in openshift-storage project
    Returns:
        (int) : Total capacity of the ceph cluster
    """
    ct_pod = pod.get_ceph_tools_pod()
    status = ct_pod.exec_ceph_cmd('ceph status')
    total_cap = status['pgmap']['bytes_total']
    total_cap_gb = total_cap / constants.GB
    return int(total_cap_gb)


def check_volsize_app_pod(**kwargs):
    """
    function to write a file on to the rbd volume.
    Args:
        **kwargs: Project name and app pod name
    Returns:
        (bool): True if the PVC size and the volume size
        in app pod is same, False otherwise
    """
    command = 'df --output=size -h /var/lib/www/html'
    size = run_cmd(command)
    size = int(size.split('\n')[-2][:-1])
    if size == kwargs['pvc_size']:
        return True
    else:
        return False


@tier3
@pytest.mark.usefixtures(
    test_fixture.__name__,
)
class TestAllocateSizeMorethanClusterSize(ManageTest):
    """
    Automates the following test

    OCS-269 - FT-OCP-Create-PV-AllocateSizeMoreThanClusterSize
    Verify a PVC creation by allocating more storage
    than what is available in Ceph
    """

    @pytest.mark.polarion_id("OCS-269")
    def test_allocate_more_size_rbd(self):
        """
        Test to validate OCS-269 for RBD volume
        """
        global pod_obj
        global pvc_obj

        pvc_size = str(ceph_storage_capacity() + 100)
        pvc_obj = helpers.create_pvc(
            sc_name=self.sc_obj.name,
            pvc_name='over-sized-pvc',
            pvc_size=pvc_size
        )
        pod_data = templating.load_yaml_to_dict(constants.CSI_RBD_POD_YAML)
        pod_data['metadata']['name'] = helpers.create_unique_resource_name(
            'test', 'pod'
        )
        pod_data['metadata']['namespace'] = defaults.ROOK_CLUSTER_NAMESPACE
        pod_data['spec']['volumes'][0]['persistentVolumeClaim']['claimName'] = \
            'over-sized-pvc'

        pod_obj = helpers.create_pod(**pod_data)
        size = pod_obj.exec_cmd_on_pod(
            'df --output=size -h /var/lib/www/html'
        ).split()[-1][0:-1]
        assert int(pvc_size)*.9 <= float(size) <= int(pvc_size)*1.1,\
            "PVC size & volume size in app pod doesn't match"
