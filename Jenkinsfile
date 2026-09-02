@Library("edge-jenkins-lib") _

pipeline {
    agent { 
        kubernetes {
            yaml readTrusted('pod.yaml')
        }
    }
    stages {

        stage('PushToPypi') {
            steps {
              container('ucpe-jenkins-general') {
                script {
                    // Push Python Package to Pypi
                    pushPyPackage()
                    }
                }
              }
            }
            stage('get_commit_msg') {
                steps {
                    script {
                        env.GIT_COMMIT_MSG = sh (script: 'git log --format="medium" -1 ${GIT_COMMIT}', returnStdout: true).trim()
                    }
                }
            }
            stage('get_commit_hash') {
                steps {
                    script {
                        env.GIT_COMMIT_HASH = sh (script: 'git log -1 --pretty=%H ${GIT_COMMIT}', returnStdout: true).trim()
                    }
                }
            }
        }
}
