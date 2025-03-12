from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import logging
from typing import TYPE_CHECKING

import sqlalchemy.orm.attributes

from ...db_socket.helpers import insert_general

try:
    from pydantic.v1 import BaseModel, Extra
except ImportError:
    from pydantic import BaseModel, Extra
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import array_agg, aggregate_order_by
from sqlalchemy.orm import lazyload, selectinload, joinedload, defer, undefer

from qcfractal.components.optimization.record_db_models import (
    OptimizationSpecificationORM,
)
from qcfractal.components.services.db_models import ServiceQueueORM, ServiceDependencyORM
from qcfractal.components.singlepoint.record_db_models import QCSpecificationORM
from qcportal.exceptions import MissingDataError
from qcportal.metadata_models import InsertMetadata, InsertCountsMetadata
from qcportal.molecules import Molecule
from qcportal.optimization import OptimizationSpecification
from qcportal.record_models import PriorityEnum, RecordStatusEnum, OutputTypeEnum
from qcportal.torsiondrive import (
    serialize_key,
    TorsiondriveSpecification,
    TorsiondriveQueryFilters,
    TorsiondriveInput,
    TorsiondriveMultiInput,
)
from qcportal.utils import hash_dict, is_included
from .record_db_models import (
    TorsiondriveSpecificationORM,
    TorsiondriveInitialMoleculeORM,
    TorsiondriveOptimizationORM,
    TorsiondriveRecordORM,
)
from ..record_socket import BaseRecordSocket

# Torsiondrive package is optional
_td_spec = importlib.util.find_spec("torsiondrive")

if _td_spec is not None:
    _td_api_spec = importlib.util.find_spec("torsiondrive.td_api")

    torsiondrive = importlib.util.module_from_spec(_td_spec)
    td_api = importlib.util.module_from_spec(_td_api_spec)

    _td_spec.loader.exec_module(torsiondrive)
    _td_api_spec.loader.exec_module(td_api)


if TYPE_CHECKING:
    from sqlalchemy.orm.session import Session
    from qcfractal.db_socket.socket import SQLAlchemySocket
    from typing import List, Dict, Tuple, Optional, Sequence, Any, Union, Iterable


class TorsiondriveServiceState(BaseModel):
    """
    This represents the current state of a torsiondrive service
    """

    class Config(BaseModel.Config):
        extra = Extra.forbid
        allow_mutation = True
        validate_assignment = True

    torsiondrive_state = {}

    # These are stored as JSON (ie, dict encoded into a string)
    # This makes for faster loads and makes them somewhat tamper-proof
    molecule_template: str
    dihedral_template: str


# Meaningless, but unique to torsiondrives
torsiondrive_insert_lock_id = 14200
torsiondrive_spec_insert_lock_id = 14201


class TorsiondriveRecordSocket(BaseRecordSocket):
    """
    Socket for handling torsiondrive computations
    """

    # Used by the base class
    record_orm = TorsiondriveRecordORM
    record_input_type = TorsiondriveInput
    record_multi_input_type = TorsiondriveMultiInput

    def __init__(self, root_socket: SQLAlchemySocket):
        BaseRecordSocket.__init__(self, root_socket)
        self._logger = logging.getLogger(__name__)

    def available(self) -> bool:
        return _td_spec is not None

    @staticmethod
    def get_children_select() -> List[Any]:
        stmt = select(
            TorsiondriveOptimizationORM.torsiondrive_id.label("parent_id"),
            TorsiondriveOptimizationORM.optimization_id.label("child_id"),
        )
        return [stmt]

    def initialize_service(
        self,
        session: Session,
        service_orm: ServiceQueueORM,
    ):
        td_orm: TorsiondriveRecordORM = service_orm.record
        specification = TorsiondriveSpecification(**td_orm.specification.model_dict())
        initial_molecules: List[Dict[str, Any]] = [x.molecule.model_dict() for x in td_orm.initial_molecules]
        keywords = specification.keywords

        # Create a template from the first initial molecule
        # we will assume they all have the same symbols, etc
        # TODO - can simplify this after removing numpy from db (ie, just copy initial_molecules[0])
        molecule_template = Molecule(**initial_molecules[0]).dict(encoding="json")
        molecule_template.pop("id", None)
        molecule_template.pop("identifiers", None)

        # The torsiondrive package uses print, so capture that using contextlib
        # Also capture any warnings generated by that package
        logging.captureWarnings(True)
        td_stdout = io.StringIO()
        with contextlib.redirect_stdout(td_stdout):
            td_state = td_api.create_initial_state(
                dihedrals=keywords.dihedrals,
                grid_spacing=keywords.grid_spacing,
                elements=molecule_template["symbols"],
                init_coords=[x["geometry"].tolist() for x in initial_molecules],
                dihedral_ranges=keywords.dihedral_ranges,
                energy_decrease_thresh=keywords.energy_decrease_thresh,
                energy_upper_limit=keywords.energy_upper_limit,
            )

        logging.captureWarnings(False)
        stdout = td_stdout.getvalue()

        # Build dihedral template. Just for convenience later
        dihedral_template = []
        for idx in keywords.dihedrals:
            tmp = {"type": "dihedral", "indices": idx}
            dihedral_template.append(tmp)

        dihedral_template_str = json.dumps(dihedral_template)
        molecule_template_str = json.dumps(molecule_template)

        self.root_socket.records.append_output(session, td_orm, OutputTypeEnum.stdout, stdout)

        service_state = TorsiondriveServiceState(
            torsiondrive_state=td_state,
            dihedral_template=dihedral_template_str,
            molecule_template=molecule_template_str,
        )

        service_orm.service_state = service_state.dict()
        sqlalchemy.orm.attributes.flag_modified(service_orm, "service_state")

    def iterate_service(
        self,
        session: Session,
        service_orm: ServiceQueueORM,
    ) -> bool:
        td_orm: TorsiondriveRecordORM = service_orm.record

        # Always update with the current provenance
        td_orm.compute_history[-1].provenance = {
            "creator": "torsiondrive",
            "version": torsiondrive.__version__,
            "routine": "torsiondrive.td_api",
        }

        # Load the state from the service_state column
        service_state = TorsiondriveServiceState(**service_orm.service_state)

        # Sort by position
        # Fully sorting by the key is not important since that ends up being a key in the dict
        # All that matters is that position 1 for a particular key comes before position 2, etc
        complete_tasks = sorted(service_orm.dependencies, key=lambda x: x.extras["position"])

        # Populate task results needed by the torsiondrive package
        task_results = {}
        for task in complete_tasks:
            td_api_key = task.extras["td_api_key"]
            task_results.setdefault(td_api_key, [])

            # This is an ORM for an optimization
            opt_record = task.record

            # Lookup molecules
            initial_id = opt_record.initial_molecule_id
            final_id = opt_record.final_molecule_id
            mol_ids = [initial_id, final_id]
            mol_data = self.root_socket.molecules.get(molecule_id=mol_ids, include=["geometry"], session=session)

            # Use plain lists rather than numpy arrays
            initial_mol_geom = mol_data[0]["geometry"].tolist()
            final_mol_geom = mol_data[1]["geometry"].tolist()

            task_results[td_api_key].append((initial_mol_geom, final_mol_geom, opt_record.energies[-1]))

        # The torsiondrive package uses print, so capture that using contextlib
        # Also capture any warnings generated by that package
        td_stdout = io.StringIO()
        logging.captureWarnings(True)
        with contextlib.redirect_stdout(td_stdout):
            td_api.update_state(service_state.torsiondrive_state, task_results)
            next_tasks = td_api.next_jobs_from_state(service_state.torsiondrive_state, verbose=True)

        stdout_append = "\n" + td_stdout.getvalue()
        logging.captureWarnings(False)

        # If there are any tasks left, submit them
        if len(next_tasks) > 0:
            self._submit_optimizations(session, service_state, service_orm, next_tasks)
        else:
            # check that what we have is consistent with what the torsiondrive package reports
            lowest_energies = td_api.collect_lowest_energies(service_state.torsiondrive_state)
            lowest_energies = {serialize_key(x): y for x, y in lowest_energies.items()}

            our_energies = {x.key: [] for x in td_orm.optimizations}
            for x in td_orm.optimizations:
                if x.energy is not None:
                    our_energies[x.key].append(x.energy)

            min_energies = {x: min(y) if y else None for x, y in our_energies.items()}
            if lowest_energies != min_energies:
                raise RuntimeError("Minimum energies reported by the torsiondrive package do not match ours!")

        # append to the existing stdout
        self.root_socket.records.append_output(session, td_orm, OutputTypeEnum.stdout, stdout_append)

        # Set the new service state. We must then mark it as modified
        # so that SQLAlchemy can pick up changes. This is because SQLAlchemy
        # cannot track mutations in nested dicts
        service_orm.service_state = service_state.dict()
        sqlalchemy.orm.attributes.flag_modified(service_orm, "service_state")

        # Return True to indicate that this service has successfully completed
        return len(next_tasks) == 0

    def _submit_optimizations(
        self,
        session: Session,
        service_state: TorsiondriveServiceState,
        service_orm: ServiceQueueORM,
        next_tasks: Dict[str, Any],
    ):
        """
        Submit the next batch of optimizations for a torsiondrive service
        """

        td_orm: TorsiondriveRecordORM = service_orm.record

        # delete all existing entries in the dependency list
        service_orm.dependencies = []

        # Create an optimization input based on the new geometry and the optimization template
        opt_spec = td_orm.specification.optimization_specification.model_dict()

        # Convert to an input
        opt_spec = OptimizationSpecification(**opt_spec).dict()

        for td_api_key, geometries in next_tasks.items():
            # Make a deep copy to prevent modifying the original ORM
            opt_spec2 = copy.deepcopy(opt_spec)

            # Construct constraints
            constraints = json.loads(service_state.dihedral_template)

            grid_id = td_api.grid_id_from_string(td_api_key)
            for con_num, k in enumerate(grid_id):
                constraints[con_num]["value"] = k

            # update the constraints
            opt_spec2["keywords"].setdefault("constraints", {})
            opt_spec2["keywords"]["constraints"].setdefault("set", [])
            opt_spec2["keywords"]["constraints"]["set"].extend(constraints)

            # Loop over the new geometries from the torsiondrive package
            constrained_mols = []
            for geometry in geometries:
                # Build new molecule
                mol = json.loads(service_state.molecule_template)
                mol["geometry"] = geometry

                constrained_mols.append(Molecule(**mol))

            # Submit the new optimizations
            meta, opt_ids = self.root_socket.records.optimization.add(
                constrained_mols,
                OptimizationSpecification(**opt_spec2),
                service_orm.compute_tag,
                service_orm.compute_priority,
                td_orm.owner_user_id,
                td_orm.owner_group_id,
                service_orm.find_existing,
                session=session,
            )

            if not meta.success:
                raise RuntimeError("Error adding optimizations - likely a developer error: " + meta.error_string)

            # ids will be in the same order as the molecules (and the geometries from td)
            opt_key = serialize_key(grid_id)
            for position, opt_id in enumerate(opt_ids):
                svc_dep = ServiceDependencyORM(
                    record_id=opt_id,
                    extras={"td_api_key": td_api_key, "position": position},
                )

                # The position field is handled by the collection class in sqlalchemy
                # corresponds to the absolute position across all optimizations for this torsiondrive,
                # not the position of the geometry for this td_api_key (as stored in the ServiceDependenciesORM)
                opt_history = TorsiondriveOptimizationORM(
                    torsiondrive_id=service_orm.record_id,
                    optimization_id=opt_id,
                    key=opt_key,
                )

                service_orm.dependencies.append(svc_dep)
                td_orm.optimizations.append(opt_history)

    def add_specifications(
        self, td_specs: Sequence[TorsiondriveSpecification], *, session: Optional[Session] = None
    ) -> Tuple[InsertMetadata, List[int]]:
        """
        Adds specifications for torsiondrive services to the database, returning their IDs.

        If an identical specification exists, then no insertion takes place and the id of the existing
        specification is returned.

        Specification IDs are returned in the same order as the input specifications

        Parameters
        ----------
        td_specs
            Sequence of specifications to add to the database
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            Metadata about the insertion, and the IDs of the specifications.
        """

        to_add = []

        for td_spec in td_specs:
            td_kw_dict = td_spec.keywords.dict()

            td_spec_dict = {"program": td_spec.program, "keywords": td_kw_dict, "protocols": {}}
            td_spec_hash = hash_dict(td_spec_dict)

            td_spec_orm = TorsiondriveSpecificationORM(
                program=td_spec.program,
                keywords=td_kw_dict,
                protocols=td_spec_dict["protocols"],
                specification_hash=td_spec_hash,
            )

            to_add.append(td_spec_orm)

        with self.root_socket.optional_session(session, False) as session:

            opt_specs = [x.optimization_specification for x in td_specs]
            meta, opt_spec_ids = self.root_socket.records.optimization.add_specifications(opt_specs, session=session)

            if not meta.success:
                return (
                    InsertMetadata(
                        error_description="Unable to add optimization specifications: " + meta.error_string,
                    ),
                    [],
                )

            assert len(opt_spec_ids) == len(td_specs)
            for td_spec_orm, opt_spec_id in zip(to_add, opt_spec_ids):
                td_spec_orm.optimization_specification_id = opt_spec_id

            meta, ids = insert_general(
                session,
                to_add,
                (
                    TorsiondriveSpecificationORM.specification_hash,
                    TorsiondriveSpecificationORM.optimization_specification_id,
                ),
                (TorsiondriveSpecificationORM.id,),
                torsiondrive_spec_insert_lock_id,
            )

            return meta, [x[0] for x in ids]

    def add_specification(
        self, td_spec: TorsiondriveSpecification, *, session: Optional[Session] = None
    ) -> Tuple[InsertMetadata, Optional[int]]:
        """
        Adds a specification for a torsiondrive service to the database, returning its id.

        If an identical specification exists, then no insertion takes place and the id of the existing
        specification is returned.

        Parameters
        ----------
        td_spec
            Specification to add to the database
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            Metadata about the insertion, and the id of the specification.
        """

        meta, ids = self.add_specifications([td_spec], session=session)

        if not ids:
            return meta, None

        return meta, ids[0]

    def get(
        self,
        record_ids: Sequence[int],
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        missing_ok: bool = False,
        *,
        session: Optional[Session] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        options = []
        if include:
            # Initial molecules will get both the ids and the actual molecule
            if is_included("initial_molecules", include, exclude, False):
                options.append(
                    selectinload(TorsiondriveRecordORM.initial_molecules).joinedload(
                        TorsiondriveInitialMoleculeORM.molecule
                    )
                )
            elif is_included("initial_molecules_ids", include, exclude, False):
                options.append(selectinload(TorsiondriveRecordORM.initial_molecules))

            if is_included("optimizations", include, exclude, False):
                options.append(selectinload(TorsiondriveRecordORM.optimizations))

            if is_included("minimum_optimizations", include, exclude, False):
                options.append(undefer(TorsiondriveRecordORM.minimum_optimizations))

        with self.root_socket.optional_session(session, True) as session:
            return self.root_socket.records.get_base(
                orm_type=self.record_orm,
                record_ids=record_ids,
                include=include,
                exclude=exclude,
                missing_ok=missing_ok,
                additional_options=options,
                session=session,
            )

    def query(
        self,
        query_data: TorsiondriveQueryFilters,
        *,
        session: Optional[Session] = None,
    ) -> List[int]:
        and_query = []
        need_spspec_join = False
        need_optspec_join = False
        need_initmol_join = False

        if query_data.qc_program is not None:
            and_query.append(QCSpecificationORM.program.in_(query_data.qc_program))
            need_spspec_join = True
        if query_data.qc_method is not None:
            and_query.append(QCSpecificationORM.method.in_(query_data.qc_method))
            need_spspec_join = True
        if query_data.qc_basis is not None:
            and_query.append(QCSpecificationORM.basis.in_(query_data.qc_basis))
            need_spspec_join = True
        if query_data.optimization_program is not None:
            and_query.append(OptimizationSpecificationORM.program.in_(query_data.optimization_program))
            need_optspec_join = True
        if query_data.initial_molecule_id is not None:
            and_query.append(TorsiondriveInitialMoleculeORM.molecule_id.in_(query_data.initial_molecule_id))
            need_initmol_join = True

        stmt = select(TorsiondriveRecordORM.id)

        # We don't search for anything td-specification specific, so no need for
        # need_tdspec_join (for now...)

        if need_optspec_join or need_spspec_join:
            stmt = stmt.join(TorsiondriveRecordORM.specification)
            stmt = stmt.join(TorsiondriveSpecificationORM.optimization_specification)

        if need_spspec_join:
            stmt = stmt.join(OptimizationSpecificationORM.qc_specification)

        if need_initmol_join:
            stmt = stmt.join(
                TorsiondriveInitialMoleculeORM,
                TorsiondriveInitialMoleculeORM.torsiondrive_id == TorsiondriveRecordORM.id,
            )

        stmt = stmt.where(*and_query)

        return self.root_socket.records.query_base(
            stmt=stmt,
            orm_type=TorsiondriveRecordORM,
            query_data=query_data,
            session=session,
        )

    def add_internal(
        self,
        initial_molecule_ids: Sequence[Iterable[int]],
        td_spec_id: int,
        as_service: bool,
        compute_tag: str,
        compute_priority: PriorityEnum,
        owner_user_id: Optional[int],
        owner_group_id: Optional[int],
        find_existing: bool,
        *,
        session: Optional[Session] = None,
    ) -> Tuple[InsertMetadata, List[Optional[int]]]:
        """
        Internal function for adding new torsiondrive computations

        This function expects that the molecules and specification are already added to the
        database and that the ids are known.

        This checks if the calculations already exist in the database. If so, it returns
        the existing id, otherwise it will insert it and return the new id.

        Parameters
        ----------
        initial_molecule_ids
            IDs of the initial sets of molecules to start the torsiondrive. One record will be added per set.
        td_spec_id
            ID of the specification
        compute_tag
            The tag for the task. This will assist in routing to appropriate compute managers.
        compute_priority
            The priority for the computation
        owner_user_id
            ID of the user who owns the record
        owner_group_id
            ID of the group with additional permission for these records
        find_existing
            If True, search for existing records and return those. If False, always add new records
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            Metadata about the insertion, and a list of record ids. The ids will be in the
            order of the input molecules
        """

        compute_tag = compute_tag.lower()

        with self.root_socket.optional_session(session, False) as session:
            self.root_socket.users.assert_group_member(owner_user_id, owner_group_id, session=session)

            # Lock for the entire transaction
            session.execute(select(func.pg_advisory_xact_lock(torsiondrive_insert_lock_id))).scalar()

            td_ids = []
            inserted_idx = []
            existing_idx = []

            if find_existing:
                # Torsiondrives are a bit more complicated because we have a many-to-many relationship
                # between torsiondrives and initial molecules. So skip the general insert
                # function and do this one at a time

                # Create a cte with the initial molecules we can query against
                # This is like a table, with the specification id and the initial molecule ids
                # as a postgres array (sorted)
                # We then use this to determine if there are duplicates
                init_mol_cte = (
                    select(
                        TorsiondriveRecordORM.id,
                        TorsiondriveRecordORM.specification_id,
                        array_agg(
                            aggregate_order_by(
                                TorsiondriveInitialMoleculeORM.molecule_id,
                                TorsiondriveInitialMoleculeORM.molecule_id.asc(),
                            )
                        ).label("molecule_ids"),
                    )
                    .join(
                        TorsiondriveInitialMoleculeORM,
                        TorsiondriveInitialMoleculeORM.torsiondrive_id == TorsiondriveRecordORM.id,
                    )
                    .group_by(TorsiondriveRecordORM.id)
                    .cte()
                )

                for idx, mol_ids in enumerate(initial_molecule_ids):
                    # sort molecules by increasing ids, and remove duplicates
                    mol_ids = sorted(set(mol_ids))

                    # does this exist?
                    stmt = select(init_mol_cte.c.id)
                    stmt = stmt.where(init_mol_cte.c.specification_id == td_spec_id)
                    stmt = stmt.where(init_mol_cte.c.molecule_ids == mol_ids)
                    existing = session.execute(stmt).scalars().first()

                    if not existing:
                        td_orm = TorsiondriveRecordORM(
                            is_service=as_service,
                            specification_id=td_spec_id,
                            status=RecordStatusEnum.waiting,
                            owner_user_id=owner_user_id,
                            owner_group_id=owner_group_id,
                        )

                        self.create_service(td_orm, compute_tag, compute_priority, find_existing)

                        session.add(td_orm)
                        session.flush()

                        for mid in mol_ids:
                            mid_orm = TorsiondriveInitialMoleculeORM(molecule_id=mid, torsiondrive_id=td_orm.id)
                            session.add(mid_orm)

                        session.flush()

                        td_ids.append(td_orm.id)
                        inserted_idx.append(idx)
                    else:
                        td_ids.append(existing)
                        existing_idx.append(idx)
            else:  # not finding existing - just add all
                for idx, mol_ids in enumerate(initial_molecule_ids):
                    # sort molecules by increasing ids, and remove duplicates
                    mol_ids = sorted(set(mol_ids))

                    td_orm = TorsiondriveRecordORM(
                        is_service=as_service,
                        specification_id=td_spec_id,
                        status=RecordStatusEnum.waiting,
                        owner_user_id=owner_user_id,
                        owner_group_id=owner_group_id,
                    )

                    self.create_service(td_orm, compute_tag, compute_priority, find_existing)

                    session.add(td_orm)
                    session.flush()

                    for mid in mol_ids:
                        mid_orm = TorsiondriveInitialMoleculeORM(molecule_id=mid, torsiondrive_id=td_orm.id)
                        session.add(mid_orm)

                    session.flush()

                    td_ids.append(td_orm.id)
                    inserted_idx.append(idx)

            meta = InsertMetadata(inserted_idx=inserted_idx, existing_idx=existing_idx)
            return meta, td_ids

    def add(
        self,
        initial_molecules: Sequence[Iterable[Union[int, Molecule]]],
        td_spec: TorsiondriveSpecification,
        as_service: bool,
        compute_tag: str,
        compute_priority: PriorityEnum,
        owner_user: Optional[str],
        owner_group: Optional[str],
        find_existing: bool,
        *,
        session: Optional[Session] = None,
    ) -> Tuple[InsertMetadata, List[Optional[int]]]:
        """
        Adds new torsiondrive calculations

        This checks if the calculations already exist in the database. If so, it returns
        the existing id, otherwise it will insert it and return the new id.

        Parameters
        ----------
        initial_molecules
            Initial sets of molecules to start the torsiondrive. One record will be added per set.
        td_spec
            Specification for the calculations
        as_service
            Whether this record should be run as a service or as a regular calculation
        compute_tag
            The tag for the task. This will assist in routing to appropriate compute managers.
        compute_priority
            The priority for the computation
        owner_user
            Name of the user who owns the record
        owner_group
            Group with additional permission for these records
        find_existing
            If True, search for existing records and return those. If False, always add new records
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            Metadata about the insertion, and a list of record ids. The ids will be in the
            order of the input molecules
        """

        with self.root_socket.optional_session(session, False) as session:
            owner_user_id, owner_group_id = self.root_socket.users.get_owner_ids(
                owner_user, owner_group, session=session
            )

            # First, add the specification
            spec_meta, spec_id = self.add_specification(td_spec, session=session)
            if not spec_meta.success:
                return (
                    InsertMetadata(
                        error_description="Aborted - could not add specification: " + spec_meta.error_string
                    ),
                    [],
                )

            # Now the molecules
            init_mol_ids = []
            for init_mol in initial_molecules:
                mol_meta, mol_ids = self.root_socket.molecules.add_mixed(init_mol, session=session)
                if not mol_meta.success:
                    return (
                        InsertMetadata(
                            error_description="Aborted - could not add all molecules: " + mol_meta.error_string
                        ),
                        [],
                    )

                init_mol_ids.append(mol_ids)

            return self.add_internal(
                init_mol_ids,
                spec_id,
                as_service,
                compute_tag,
                compute_priority,
                owner_user_id,
                owner_group_id,
                find_existing,
                session=session,
            )

    def add_from_input(
        self,
        record_input: TorsiondriveInput,
        compute_tag: str,
        compute_priority: PriorityEnum,
        owner_user: Optional[Union[int, str]],
        owner_group: Optional[Union[int, str]],
        find_existing: bool,
        *,
        session: Optional[Session] = None,
    ) -> Tuple[InsertCountsMetadata, int]:

        assert isinstance(record_input, TorsiondriveInput)

        meta, ids = self.add(
            [record_input.initial_molecules],
            record_input.specification,
            compute_tag,
            compute_priority,
            owner_user,
            owner_group,
            find_existing,
        )

        return InsertCountsMetadata.from_insert_metadata(meta), ids[0]

    ####################################################
    # Some stuff to be retrieved for torsiondrives
    ####################################################

    def get_initial_molecules_ids(
        self,
        record_id: int,
        *,
        session: Optional[Session] = None,
    ) -> List[int]:
        """
        Obtain the initial molecules of a torsiondrive

        Parameters
        ----------
        record_id
            ID of the torsiondrive to get the minimum optimizations of
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            List of Molecule ids
        """

        options = [
            lazyload("*"),
            defer("*"),
            joinedload(TorsiondriveRecordORM.initial_molecules).options(
                undefer(TorsiondriveInitialMoleculeORM.molecule_id)
            ),
        ]

        with self.root_socket.optional_session(session) as session:
            rec = session.get(TorsiondriveRecordORM, record_id, options=options)
            if rec is None:
                raise MissingDataError(f"Cannot find record {record_id}")
            return [x.molecule_id for x in rec.initial_molecules]

    def get_optimizations(
        self,
        record_id: int,
        *,
        session: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        options = [lazyload("*"), defer("*"), joinedload(TorsiondriveRecordORM.optimizations).options(undefer("*"))]

        with self.root_socket.optional_session(session) as session:
            rec = session.get(TorsiondriveRecordORM, record_id, options=options)
            if rec is None:
                raise MissingDataError(f"Cannot find record {record_id}")
            return [x.model_dict() for x in rec.optimizations]

    def get_minimum_optimizations(
        self,
        record_id: int,
        *,
        session: Optional[Session] = None,
    ) -> Dict[str, int]:
        """
        Obtain the records for the minimum optimizations for a torsiondrive

        Parameters
        ----------
        record_id
            ID of the torsiondrive to get the minimum optimizations of
        session
            An existing SQLAlchemy session to use. If None, one will be created. If an existing session
            is used, it will be flushed (but not committed) before returning from this function.

        Returns
        -------
        :
            Dictionary, where the value is an optimization record (as a dictionary) and the key is the key of the
            optimization in the torsiondrive (representing the angles)
        """

        stmt = select(TorsiondriveRecordORM.minimum_optimizations).where(TorsiondriveRecordORM.id == record_id)

        with self.root_socket.optional_session(session, True) as session:
            r = session.execute(stmt).scalar_one_or_none()  # List of (key, id)
            return {} if r is None else r
