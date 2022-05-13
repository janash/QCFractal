import json
from typing import Dict, Any, Tuple

from qcfractal import FractalSnowflake
from qcfractal.db_socket import SQLAlchemySocket
from qcfractaltesting import load_procedure_data, geoip_path, test_users
from qcportal import PortalClient, ManagerClient
from qcportal.managers import ManagerName
from qcportal.permissions import UserInfo
from qcportal.records import PriorityEnum, RecordStatusEnum
from qcportal.utils import recursive_normalizer

mname1 = ManagerName(cluster="test_cluster", hostname="a_host", uuid="1234-5678-1234-5678")
mname2 = ManagerName(cluster="test_cluster", hostname="a_host", uuid="2234-5678-1234-5678")


class TestingSnowflake(FractalSnowflake):
    """
    A snowflake class used for testing

    This mostly behaves like FractalSnowflake, but
    allows for some extra features such as manual handling of periodics
    and creating storage sockets from an instance.

    By default, the periodics and worker subprocesses are not started.
    """

    def __init__(
        self,
        database_config,
        encoding: str,
        start_flask=True,
        create_users=False,
        enable_security=False,
        allow_unauthenticated_read=False,
        log_access=True,
    ):

        self._encoding = encoding

        # Tighten the service frequency for tests
        # Also disable connection pooling in the storage socket
        # (which can leave db connections open, causing problems when we go to delete
        # the database)
        # Have a short token expiration (in case enable_security is True)
        extra_config = {}

        api_config = {"jwt_access_token_expires": 1}  # expire tokens in 1 second

        # also tighten the server return limit (so we can test chunking)
        api_limits = {"manager_tasks_claim": 5, "manager_tasks_return": 2}

        extra_config["api"] = api_config
        extra_config["api_limits"] = api_limits

        extra_config["enable_security"] = enable_security
        extra_config["allow_unauthenticated_read"] = allow_unauthenticated_read
        extra_config["service_frequency"] = 5
        extra_config["loglevel"] = "DEBUG"
        extra_config["heartbeat_frequency"] = 3
        extra_config["heartbeat_max_missed"] = 2
        extra_config["statistics_frequency"] = 3

        extra_config["database"] = {"pool_size": 0}
        extra_config["log_access"] = log_access
        extra_config["geo_file_path"] = geoip_path

        FractalSnowflake.__init__(
            self,
            start=False,
            compute_workers=0,
            enable_watching=True,
            database_config=database_config,
            flask_config="testing",
            extra_config=extra_config,
        )

        if create_users:
            self.create_users()

        # Start the flask api process if requested
        if start_flask:
            self.start_flask()

    def create_users(self):
        # Get a storage socket and add the roles/users/passwords
        storage = self.get_storage_socket()
        for k, v in test_users.items():
            uinfo = UserInfo(username=k, enabled=True, **v["info"])
            storage.users.add(uinfo, password=v["pw"])

    def get_storage_socket(self) -> SQLAlchemySocket:
        """
        Obtain a new SQLAlchemy socket

        This function will create a new socket instance every time it is called
        """

        return SQLAlchemySocket(self._qcf_config)

    def start_flask(self) -> None:
        """
        Starts the flask subprocess
        """
        if not self._flask_proc.is_alive():
            self._flask_proc.start()
            self.wait_for_flask()

    def stop_flask(self) -> None:
        """
        Stops the flask subprocess
        """
        if self._flask_proc.is_alive():
            self._flask_proc.stop()
            self._flask_started.clear()

    def start_periodics(self) -> None:
        """
        Starts the periodics subprocess
        """
        if not self._periodics_proc.is_alive():
            self._periodics_proc.start()

    def stop_periodics(self) -> None:
        """
        Stops the periodics subprocess
        """
        if self._periodics_proc.is_alive():
            self._periodics_proc.stop()

    def client(self, username=None, password=None) -> PortalClient:
        """
        Obtain a client connected to this snowflake

        Parameters
        ----------
        username
            The username to connect as
        password
            The password to use

        Returns
        -------
        :
            A PortalClient that is connected to this snowflake
        """
        client = PortalClient(
            self.get_uri(),
            username=username,
            password=password,
        )
        client.encoding = self._encoding
        return client

    def manager_client(self, name_data: ManagerName, username=None, password=None) -> ManagerClient:
        """
        Obtain a manager client connected to this snowflake

        Parameters
        ----------
        username
            The username to connect as
        password
            The password to use

        Returns
        -------
        :
            A PortalClient that is connected to this snowflake
        """

        # Now that we know it's up, create a manager client
        client = ManagerClient(name_data, self.get_uri(), username=username, password=password)
        client.encoding = self._encoding
        return client


def populate_db(storage_socket: SQLAlchemySocket):
    """
    Populates the db with tasks in all statuses
    """

    storage_socket.managers.activate(
        name_data=mname1,
        manager_version="v2.0",
        qcengine_version="v1.0",
        username="bill",
        programs={"psi4": None, "qchem": "v3.0", "rdkit": None, "geometric": None},
        tags=["tag1", "tag2", "tag3", "tag6"],
    )

    input_spec_0, molecule_0, result_data_0 = load_procedure_data("psi4_methane_opt_sometraj")
    input_spec_1, molecule_1, result_data_1 = load_procedure_data("psi4_water_gradient")
    input_spec_2, molecule_2, result_data_2 = load_procedure_data("psi4_water_hessian")
    input_spec_3, molecule_3, result_data_3 = load_procedure_data("psi4_methane_gradient_fail_iter")
    input_spec_4, molecule_4, result_data_4 = load_procedure_data("rdkit_water_energy")
    input_spec_5, molecule_5, result_data_5 = load_procedure_data("psi4_benzene_energy_2")
    input_spec_6, molecule_6, result_data_6 = load_procedure_data("psi4_water_energy")

    meta, id_0 = storage_socket.records.optimization.add([molecule_0], input_spec_0, "tag0", PriorityEnum.normal)
    meta, id_1 = storage_socket.records.singlepoint.add([molecule_1], input_spec_1, "tag1", PriorityEnum.high)
    meta, id_2 = storage_socket.records.singlepoint.add([molecule_2], input_spec_2, "tag2", PriorityEnum.high)
    meta, id_3 = storage_socket.records.singlepoint.add([molecule_3], input_spec_3, "tag3", PriorityEnum.high)
    meta, id_4 = storage_socket.records.singlepoint.add([molecule_4], input_spec_4, "tag4", PriorityEnum.normal)
    meta, id_5 = storage_socket.records.singlepoint.add([molecule_5], input_spec_5, "tag5", PriorityEnum.normal)
    meta, id_6 = storage_socket.records.singlepoint.add([molecule_6], input_spec_6, "tag6", PriorityEnum.normal)
    all_id = id_0 + id_1 + id_2 + id_3 + id_4 + id_5 + id_6

    # 0 = waiting   1 = complete   2 = running
    # 3 = error     4 = cancelled  5 = deleted
    # 6 = invalid

    # claim only the ones we want to be complete, running, or error (1, 2, 3, 6)
    # 6 needs to be complete to be invalidated
    tasks = storage_socket.tasks.claim_tasks(mname1.fullname, limit=4)
    assert len(tasks) == 4

    # we don't send back the one we want to be 'running' still (#2)
    storage_socket.tasks.update_finished(
        mname1.fullname,
        {
            # tasks[1] is left running (corresponds to record 2)
            tasks[0]["id"]: result_data_1,
            tasks[2]["id"]: result_data_3,
            tasks[3]["id"]: result_data_6,
        },
    )

    # Add some more entries to the history of #3 (failing)
    for i in range(4):
        meta = storage_socket.records.reset(id_3)
        assert meta.success
        tasks = storage_socket.tasks.claim_tasks(mname1.fullname, limit=1)
        assert len(tasks) == 1
        assert tasks[0]["tag"] == "tag3"

        storage_socket.tasks.update_finished(
            mname1.fullname,
            {
                tasks[0]["id"]: result_data_3,
            },
        )

    meta = storage_socket.records.cancel(id_4)
    assert meta.n_updated == 1
    meta = storage_socket.records.delete(id_5)
    assert meta.n_deleted == 1
    meta = storage_socket.records.invalidate(id_6)
    assert meta.n_updated == 1

    rec = storage_socket.records.get(all_id, include=["status"])
    assert rec[0]["status"] == RecordStatusEnum.waiting
    assert rec[1]["status"] == RecordStatusEnum.complete
    assert rec[2]["status"] == RecordStatusEnum.running
    assert rec[3]["status"] == RecordStatusEnum.error
    assert rec[4]["status"] == RecordStatusEnum.cancelled
    assert rec[5]["status"] == RecordStatusEnum.deleted
    assert rec[6]["status"] == RecordStatusEnum.invalid

    return all_id


def run_service_constropt(
    record_id: int,
    result_data: Dict[str, Any],
    storage_socket: SQLAlchemySocket,
    max_iterations: int = 20,
    activate_manager: bool = True,
) -> Tuple[bool, int]:
    """
    Runs a service that is based on constrained optimizations
    """

    # A manager for completing the tasks
    mname1 = ManagerName(cluster="test_cluster", hostname="a_host", uuid="1234-5678-1234-5678")

    if activate_manager:
        storage_socket.managers.activate(
            name_data=mname1,
            manager_version="v2.0",
            qcengine_version="v1.0",
            username="bill",
            programs={
                "geometric": None,
                "psi4": None,
            },
            tags=["*"],
        )

    rec = storage_socket.records.get([record_id], include=["*", "service"])
    assert rec[0]["status"] in [RecordStatusEnum.waiting, RecordStatusEnum.running]

    tag = rec[0]["service"]["tag"]
    priority = rec[0]["service"]["priority"]

    n_optimizations = 0
    n_iterations = 0
    r = 1

    while n_iterations < max_iterations:
        r = storage_socket.services.iterate_services()

        if r == 0:
            break

        n_iterations += 1

        rec = storage_socket.records.get(
            [record_id], include=["*", "service.*", "service.dependencies.*", "service.dependencies.record"]
        )
        assert rec[0]["status"] == RecordStatusEnum.running

        # only do 5 tasks at a time. Tests iteration when stuff is not completed
        manager_tasks = storage_socket.tasks.claim_tasks(mname1.fullname, limit=5)

        # Sometimes a task may be duplicated in the service dependencies.
        # The C8H6 test has this "feature"
        opt_ids = set(x["record_id"] for x in manager_tasks)
        opt_recs = storage_socket.records.optimization.get(opt_ids, include=["*", "initial_molecule", "task"])
        assert all(x["task"]["priority"] == priority for x in opt_recs)
        assert all(x["task"]["tag"] == tag for x in opt_recs)

        manager_ret = {}
        for opt in opt_recs:
            # Find out info about what tasks the service spawned
            mol_hash = opt["initial_molecule"]["identifiers"]["molecule_hash"]
            constraints = opt["specification"]["keywords"].get("constraints", None)

            # Lookups may depend on floating point values
            constraints = recursive_normalizer(constraints)

            # This is the key in the dictionary of optimization results
            constraints_str = json.dumps(constraints, sort_keys=True)

            optresult_key = mol_hash + "|" + constraints_str

            opt_data = result_data[optresult_key]
            manager_ret[opt["task"]["id"]] = opt_data

        rmeta = storage_socket.tasks.update_finished(mname1.fullname, manager_ret)
        assert rmeta.n_accepted == len(manager_tasks)
        n_optimizations += len(manager_ret)

    return r == 0, n_optimizations


def run_service_simple(
    record_id: int,
    result_data: Dict[str, Any],
    storage_socket: SQLAlchemySocket,
    max_iterations: int = 20,
    activate_manager: bool = True,
) -> Tuple[bool, int]:
    """
    Runs a service that is based on singlepoint calculations
    """

    # A manager for completing the tasks
    mname1 = ManagerName(cluster="test_cluster", hostname="a_host", uuid="1234-5678-1234-5678")

    if activate_manager:
        storage_socket.managers.activate(
            name_data=mname1,
            manager_version="v2.0",
            qcengine_version="v1.0",
            username="bill",
            programs={
                "geometric": None,
                "psi4": None,
            },
            tags=["*"],
        )

    rec = storage_socket.records.get([record_id], include=["*", "service"])
    assert rec[0]["status"] in [RecordStatusEnum.waiting, RecordStatusEnum.running]

    tag = rec[0]["service"]["tag"]
    priority = rec[0]["service"]["priority"]

    n_records = 0
    n_iterations = 0
    r = 1

    while n_iterations < max_iterations:
        r = storage_socket.services.iterate_services()

        if r == 0:
            break

        n_iterations += 1

        rec = storage_socket.records.get(
            [record_id], include=["*", "service.*", "service.dependencies.*", "service.dependencies.record"]
        )
        assert rec[0]["status"] == RecordStatusEnum.running

        # only do 5 tasks at a time. Tests iteration when stuff is not completed
        manager_tasks = storage_socket.tasks.claim_tasks(mname1.fullname, limit=5)

        # Sometimes a task may be duplicated in the service dependencies.
        # The C8H6 test has this "feature"
        ids = set(x["record_id"] for x in manager_tasks)
        recs = storage_socket.records.get(ids, include=["id", "record_type", "task"])
        assert all(x["task"]["priority"] == priority for x in recs)
        assert all(x["task"]["tag"] == tag for x in recs)

        manager_ret = {}
        for r in recs:
            # Find out info about what tasks the service spawned
            sock = storage_socket.records.get_socket(r["record_type"])

            # Include any molecules in the record. Unknown ones are ignored
            real_r = sock.get([r["id"]], include=["*", "molecule", "initial_molecule"])[0]

            # Optimizations have initial molecule
            if "initial_molecule" in real_r:
                mol_hash = real_r["initial_molecule"]["identifiers"]["molecule_hash"]
            else:
                mol_hash = real_r["molecule"]["identifiers"]["molecule_hash"]

            key = r["record_type"] + "|" + mol_hash

            task_result = result_data[key]
            manager_ret[r["task"]["id"]] = task_result

        rmeta = storage_socket.tasks.update_finished(mname1.fullname, manager_ret)
        assert rmeta.n_accepted == len(manager_tasks)
        n_records += len(manager_ret)

    return r == 0, n_records
