import os
import ayon_api
import subprocess
from dataclasses import dataclass, field, asdict
import pyblish.api
from datetime import datetime
from pathlib import Path

from ayon_core.lib import is_in_tests

from ayon_deadline import abstract_submit_deadline


@dataclass
class DeadlinePluginInfo:
    ProjectFile: str = field(default=None)
    Executable: str = field(default=None)
    EditorExecutableName: str = field(default=None)
    EngineVersion: str = field(default=None)
    CommandLineMode: str = field(default=True)
    OutputFilePath: str = field(default=None)
    Output: str = field(default=None)
    StartupDirectory: str = field(default=None)
    CommandLineArguments: str = field(default=None)
    PerforceStream: str = field(default=None)
    PerforceChangelist: str = field(default=None)
    PerforceGamePath: str = field(default=None)


class UnrealSubmitDeadline(
    abstract_submit_deadline.AbstractSubmitDeadline
):
    """Supports direct rendering of prepared Unreal project on Deadline
    (`render` product must be created with flag for Farm publishing) OR
    Perforce assisted rendering.

    For this Ayon server must contain `ayon-perforce` addon and provide
    configuration for it (P4 credentials etc.)!
    """

    label = "Submit Unreal to Deadline"
    order = pyblish.api.IntegratorOrder + 0.1
    hosts = ["unreal"]
    families = ["render.farm"]  # cannot be "render' as that is integrated
    targets = ["local"]

    def get_job_info(self, job_info=None):
        instance = self._instance
        
        #con = ayon_api.get_server_api_connection()
        
        #project = os.environ.get("AYON_PROJECT_NAME")
        #project_entity = ayon_api.get(f"projects/{project}")
        #render_path = project_entity["config"]["roots"]["renders"]["windows"]
        
        job_info.BatchName = self._get_batch_name()
        job_info.Plugin = "UnrealEngine5"
        #job_info.OutputDirectory[0] = render_path

        # already collected explicit values for rendered Frames
        if (
            not job_info.Frames
            and instance.data["frameEnd"] > instance.data["frameStart"]
        ):
            # Deadline requires integers in frame range
            frame_range = "{}-{}".format(
                int(round(instance.data["frameStart"])),
                int(round(instance.data["frameEnd"])))
            job_info.Frames = frame_range

        return job_info

    def get_plugin_info(self):
        deadline_plugin_info = DeadlinePluginInfo()
        
        con = ayon_api.get_server_api_connection()
        
        farm_rendering = True
        if "render.local_machine" in self._instance.data["families"]:
            farm_rendering = False
        
        project = os.environ.get("AYON_PROJECT_NAME")
        task_name = os.environ.get("AYON_TASK_NAME")
        project_entity = ayon_api.get(f"projects/{project}")
        local_project_root = project_entity["config"]["roots"]["work_unreal"]["windows"]
        project_root = project_entity["config"]["roots"]["work"]["windows"]
        render_path = project_entity["config"]["roots"]["renders"]["windows"]
        
        #project_root = os.path.join(project_root, project, "unreal_project", task_name, f"{task_name}.uproject")
        project_root = os.path.join("Y:\\", "unreal_projects", task_name, f"{task_name}.uproject")
        self.log.debug(f">>> Project root: {project_root}")
        
        if not farm_rendering:
            project_root = os.path.join(local_project_root, os.path.basename(os.path.dirname(project_root)), os.path.basename(project_root))
        
        # --- Git Variables ---------------------------------------------------------
        if farm_rendering:
            ORG = "frankvaliant"
            GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"

            auto_commit_message = "Auto commit before Rendering"
            server_project_path = os.path.dirname(project_root)
            local_project_path = f"{local_project_root}/{task_name}"
            self.log.debug(f">>> Local path: {local_project_path}")
            self.log.debug(f">>> Server path: {server_project_path}")
            self.log.debug(f">>> Project Name: {project}")
            self.log.debug(f">>> Task Name: {task_name}")

            repo_url = f"https://dev.azure.com/{ORG}/{task_name}/_git/{task_name}"

            # --- Git Commit and Push ---------------------------------------------------
            subprocess.check_call(["git", "-C", local_project_path, "remote", "set-url", "origin", repo_url])
            subprocess.check_call(["git", "-C", local_project_path, "add", "."])
            
            try:
                subprocess.check_call(["git", "-C", local_project_path, "commit", "-m", auto_commit_message])
            except subprocess.CalledProcessError as e:
                self.log.debug("Nothing to commit or an error occurred:", e)

            push_result = subprocess.run(
                ["git", "-C", local_project_path, "push", "-u", "origin", "master"],
                capture_output=True,
                text=True,
                check=False
            )

            self.log.debug(f"stdout: {push_result.stdout}")
            self.log.debug(f"stderr: {push_result.stderr}")
            self.log.debug(f"returncode: {push_result.returncode}")

            # --- Resolve Server git ownership of project -------------------------------
            result = subprocess.run(
                ["git", "config", "--global", "--get-all", "safe.directory"],
                capture_output=True,
                text=True
            )
            
            if server_project_path not in result.stdout:
                self.log.debug(f"Adding safe directory: {server_project_path}")
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", server_project_path],
                    check=True
                )
            
            # --- Pull from git on Server -----------------------------------------------
            if not os.path.exists(server_project_path):
                self.log.debug(">>> Git settings setup started...")
                subprocess.run(["git", "config", "--global", "lfs.concurrenttransfers", "32"], check=True)
                subprocess.run(["git", "config", "--global", "lfs.batch", "true"], check=True)
                subprocess.run(["git", "config", "--global", "http.lowSpeedLimit", "100"], check=True)
                subprocess.run(["git", "config", "--global", "http.lowSpeedTime", "300"], check=True)
                subprocess.run(["git", "config", "--global", "http.version", "HTTP/1.1"], check=True)
                #subprocess.check_call(["git", "clone", repo_url, server_project_path])
                #subprocess.run(["git", "clone", repo_url, server_project_path], check=True)
                # Shallow clone
                self.log.debug(f">>> Cloning repository to: {server_project_path}")
                subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, server_project_path],
                    check=True,
                    env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
                )
                # LFS pull
                self.log.debug(f">>> Git LFS pull to: {server_project_path}")
                subprocess.run(
                    ["git", "-C", server_project_path, "lfs", "pull"],
                    check=True,
                )
            else:
                lock_file = os.path.join(server_project_path, ".git", "index.lock")
                if os.path.exists(lock_file):
                    self.log.debug(f">>> Trying to remove git index.lock file...")
                    try:
                        os.remove(lock_file)
                        self.log.debug(f">>> Git index.lock file removed!")
                    except Exception as e:
                        self.log.debug(f"Failed to remove index.lock: {e}")
                    
                self.log.debug(f">>> Pulling latest changes to: {server_project_path}")
                
                subprocess.run(["git", "-C", server_project_path, "fetch", "origin"], check=True)

                subprocess.run(["git", "-C", server_project_path, "checkout", "master"], check=True)

                pull_result = subprocess.run(["git", "-C", server_project_path, "reset", "--hard", "origin/master"], capture_output=True, text=True, check=True)
                
                #subprocess.run(["git", "-C", server_project_path, "clean", "-fd"], check=False)
                
                self.log.debug(f"stdout: {pull_result.stdout}")
                self.log.debug(f"stderr: {pull_result.stderr}")
                self.log.debug(f"returncode: {pull_result.returncode}")

        #render_path = self._instance.data["expectedFiles"][0]
        #self._instance.data["outputDir"] = os.path.dirname(render_path)
        #self._instance.data["outputDir"] = os.path.dirname(render_path)
        #self._instance.data["stagingDir"] = render_path
        self._instance.data["outputDir"] = render_path
        #self._instance.context.data["instances"][0]["representations"][0]["stagingDir"]
        self._instance.context.data["version"] = 1  #TODO
        temp = self._instance.data["families"]
        self.log.debug(f"local:{temp}")

        #render_dir = os.path.dirname(render_path)
        file_name = self._instance.data["file_names"][0]
        files_render_path = os.path.join(render_path, file_name)

        #deadline_plugin_info.ProjectFile = self.scene_path
        deadline_plugin_info.ProjectFile = project_root
        deadline_plugin_info.Output = files_render_path.replace("\\", "/")
        #deadline_plugin_info.OutputFilePath = os.path.dirname(deadline_plugin_info.Output)
        
        deadline_plugin_info.EditorExecutableName = "UnrealEditor-Cmd.exe"
        deadline_plugin_info.EngineVersion = self._instance.data["app_version"]
        unreal_exe_path = (f"C:\\Program Files\\Epic Games\\UE_{deadline_plugin_info.EngineVersion}\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe")
        deadline_plugin_info.Executable = unreal_exe_path
        deadline_plugin_info.StartupDirectory = str(Path(unreal_exe_path).parent)
        
        master_level = self._instance.data["master_level"]
        master_level_name = master_level.rsplit('.', 1)[0]
        master_sequence = self._instance.data["master_sequence"]
        master_sequence_name = master_sequence.rsplit('.', 1)[0]
        render_queue_path = self._instance.data["render_queue_path"]
        cmd_args = [
            master_level_name,
            #"-game",
            f"-MoviePipelineConfig={render_queue_path}",
            f"-LevelSequence={master_sequence_name}",
            f"-Map={master_level}",
            f"-Unreal={deadline_plugin_info.EngineVersion}",
            f"-Project={project_root}",
            f"-Renders={render_path}",
            "-windowed",
            "-Log",
            "-StdOut",
            "-allowStdOutLogVerbosity",
            "-Unattended",
            "-renderoffscreen",
            "-NoSound",
            "-NoSplash",
            "-NoWindow",
            "-DDC-ForceMemoryCache",
            "-run=pythonscript",
            "-script=\\\\10.21.110.15\\technology\\deployment\\ayon_scripts\\custom_mrq_script\\create_mrq_and_render.py",
        ]
        self.log.debug(f"cmd-args: {cmd_args}")
        deadline_plugin_info.CommandLineArguments = " ".join(cmd_args)

        # if Perforce - triggered by active `changelist_metadata` instance!!
        collected_perforce = self._get_perforce_info()
        if collected_perforce:
            perforce_data = (
                self._instance.context.data.get("perforce")
                or self._instance.context.data.get("version_control")
            )
            workspace_dir = perforce_data["workspace_dir"]
            stream = perforce_data["stream"]
            self._update_perforce_data(
                self.scene_path,
                workspace_dir,
                stream,
                collected_perforce["change_info"]["change"],
                deadline_plugin_info,
            )

        return asdict(deadline_plugin_info)

    def from_published_scene(self, replace_in_path=True):
        """ Do not overwrite expected files.

            Use published is set to True, so rendering will be triggered
            from published scene (in 'publish' folder). Default implementation
            of abstract class renames expected (eg. rendered) files accordingly
            which is not needed here.
        """
        return super().from_published_scene(False)

    def _get_batch_name(self):
        """Returns value that differentiate jobs in DL.

        For automatic tests it adds timestamp, for Perforce driven change list
        """
        batch_name = os.path.basename(self._instance.data["source"])
        if is_in_tests():
            batch_name += datetime.now().strftime("%d%m%Y%H%M%S")
        collected_perforce = self._get_perforce_info()
        if collected_perforce:
            change = (collected_perforce["change_info"]["change"])
            batch_name = f"{batch_name}_{change}"
        return batch_name

    def _get_perforce_info(self):
        """Look if changelist_metadata is published to get change list info.

        Context perforce dict contains universal connection info, instance
        perforce contains detail about change list.
        """
        change_list_version = {}
        for inst in self._instance.context:
            # get change info from `changelist_metadata` instance
            inst_data = inst.data
            change_list_version = (
                inst_data.get("perforce")
                or inst_data.get("version_control")  # backward compatibility
            )
            if change_list_version:
                context_version = (
                    self._instance.context.data.get("perforce")
                    or self._instance.context.data.get("version_control")
                )
                change_list_version.update(context_version)
                break
        return change_list_version

    def _update_perforce_data(
        self,
        scene_path,
        workspace_dir,
        stream,
        change_list_id,
        deadline_plugin_info,
    ):
        """Adds Perforce metadata which causes DL pre job to sync to change.

        It triggers only in presence of activated `changelist_metadata`
        instance, which materialize info about commit. Artists could return
        to any published commit and re-render if they choose.
        `changelist_metadata` replaces `workfile` as there are no versioned
        Unreal projects (because of size).
        """
        # normalize paths, c:/ vs C:/
        scene_path = str(Path(scene_path).resolve())
        workspace_dir = str(Path(workspace_dir).resolve())

        unreal_project_file_name = os.path.basename(scene_path)

        unreal_project_hierarchy = self.scene_path.replace(workspace_dir, "")
        unreal_project_hierarchy = (
            unreal_project_hierarchy.replace(unreal_project_file_name, ""))
        # relative path from workspace dir to last folder
        unreal_project_hierarchy = unreal_project_hierarchy.strip("\\")

        deadline_plugin_info.ProjectFile = unreal_project_file_name

        deadline_plugin_info.PerforceStream = stream
        deadline_plugin_info.PerforceChangelist = change_list_id
        deadline_plugin_info.PerforceGamePath = unreal_project_hierarchy
