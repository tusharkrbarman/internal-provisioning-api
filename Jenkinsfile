def resolveScenario(testOption, platform, osName) {
    def scenarioMap = [
        'DPCPP Compiler Validation|ADL|windows-11'          : 'dpcpp-adl-win11-validation',
        'DPCPP Compiler Validation|MTL|ubuntu-24.04'        : 'cpp-mtl-linux-validation',
        'IDE Extension Validation|windows-client|windows-11': 'ide-extension-win11-validation',
        'GPU Runtime Validation|DG2|windows-11'             : 'gpu-dg2-win11-validation',
        'Package Validation|caas|linux'                     : 'package-validation-caas',
        'Static Analysis|caas|linux'                        : 'static-analysis-caas',
        'VM Smoke Validation|vm|ubuntu-24.04'               : 'oneapi-vm-smoke-validation'
    ]

    return scenarioMap["${testOption}|${platform}|${osName}"]
}

pipeline {
    agent any

    parameters {
        choice(
            name: 'TEST_OPTION',
            choices: [
                'DPCPP Compiler Validation',
                'IDE Extension Validation',
                'GPU Runtime Validation',
                'Package Validation',
                'Static Analysis',
                'VM Smoke Validation'
            ],
            description: 'Validation workflow to run'
        )
        choice(
            name: 'PLATFORM',
            choices: [
                'ADL',
                'MTL',
                'DG2',
                'windows-client',
                'caas',
                'vm'
            ],
            description: 'Target platform or execution environment'
        )
        choice(
            name: 'OS',
            choices: [
                'windows-11',
                'ubuntu-24.04',
                'linux'
            ],
            description: 'Operating system required by the validation job'
        )
        choice(
            name: 'TEAM',
            choices: [
                'oneapi',
                'compiler-validation',
                'ide-validation',
                'gpu-validation',
                'package-validation'
            ],
            description: 'Team tag used for resource filtering'
        )
        string(
            name: 'PROVISION_API',
            defaultValue: 'http://65.2.79.175:8080',
            description: 'Middleware provisioning API base URL'
        )
        string(
            name: 'DURATION_HOURS',
            defaultValue: '4',
            description: 'Reservation duration in hours'
        )
    }

    environment {
        REQUEST_ID = ''
        RESERVATION_ID = ''
        MACHINE_ID = ''
        SELECTED_SCENARIO = ''
    }

    stages {
        stage('Resolve Scenario') {
            steps {
                script {
                    def key = "${params.TEST_OPTION}|${params.PLATFORM}|${params.OS}"
                    def scenario = resolveScenario(params.TEST_OPTION, params.PLATFORM, params.OS)
                    echo "Scenario lookup key: ${key}"

                    if (!scenario) {
                        error """
Unsupported validation selection:
  TEST_OPTION=${params.TEST_OPTION}
  PLATFORM=${params.PLATFORM}
  OS=${params.OS}

Choose one of the supported combinations defined in the Jenkinsfile scenarioMap.
"""
                    }

                    env.SELECTED_SCENARIO = scenario.toString()
                    writeFile file: 'selected_scenario.txt', text: scenario.toString()
                    echo "Selected scenario: ${env.SELECTED_SCENARIO}"
                }
            }
        }

        stage('Validate Agent Tools') {
            steps {
                sh '''
                    command -v curl >/dev/null 2>&1 || {
                      echo "curl is required on the Jenkins agent. Install it with: sudo apt install -y curl"
                      exit 127
                    }
                    command -v sed >/dev/null 2>&1 || {
                      echo "sed is required on the Jenkins agent."
                      exit 127
                    }
                '''
            }
        }

        stage('Checkout Source') {
            steps {
                sh '''
                    echo "Checking out product source for ${SELECTED_SCENARIO}"
                    echo "Simulating source sync and dependency metadata setup..."
                    sleep 10
                '''
            }
        }

        stage('Build Package') {
            steps {
                sh '''
                    echo "Building oneAPI validation package for ${SELECTED_SCENARIO}"
                    echo "Simulating compile, package, and artifact staging..."
                    sleep 10
                '''
            }
        }

        stage('Provision Environment') {
            steps {
                script {
                    def scenario = resolveScenario(params.TEST_OPTION, params.PLATFORM, params.OS)
                    if (!scenario) {
                        error "Unsupported selection before provisioning: TEST_OPTION=${params.TEST_OPTION}, PLATFORM=${params.PLATFORM}, OS=${params.OS}"
                    }

                    def selectedScenario = fileExists('selected_scenario.txt')
                        ? readFile('selected_scenario.txt').trim()
                        : scenario.toString()

                    env.SELECTED_SCENARIO = selectedScenario

                    if (!selectedScenario?.trim() || selectedScenario == 'null') {
                        error "Selected scenario is empty. Check TEST_OPTION=${params.TEST_OPTION}, PLATFORM=${params.PLATFORM}, OS=${params.OS}"
                    }

                    writeFile file: 'provision_payload.json', text: """
                    {
                      "test_scenario": "${selectedScenario}",
                      "team": "${params.TEAM}",
                      "jenkins_build_id": "${env.BUILD_NUMBER}",
                      "duration_hours": ${params.DURATION_HOURS}
                    }
                    """

                    def provisionHttpCode = sh(
                        script: """
                            curl -sS -o provision_response.json -w '%{http_code}' \
                              -X POST '${params.PROVISION_API}/provision' \
                              -H 'Content-Type: application/json' \
                              --data-binary @provision_payload.json
                        """,
                        returnStdout: true
                    ).trim()

                    def responseText = readFile('provision_response.json').trim()

                    echo "Provision HTTP status: ${provisionHttpCode}"
                    echo "Provision response: ${responseText}"
                    if (!(provisionHttpCode in ['200', '201', '202'])) {
                        error "Provisioning API returned HTTP ${provisionHttpCode}: ${responseText}"
                    }

                    env.REQUEST_ID = sh(
                        script: '''
                            sed -n 's/.*"request_id":"\\([^"]*\\)".*/\\1/p' provision_response.json
                        ''',
                        returnStdout: true
                    ).trim()

                    if (!env.REQUEST_ID?.trim()) {
                        error "Provisioning API did not return a request_id. HTTP ${provisionHttpCode}. Body: ${responseText}"
                    }
                }
            }
        }

        stage('Wait For Ready') {
            steps {
                script {
                    timeout(time: 15, unit: 'MINUTES') {
                        waitUntil {
                            def statusHttpCode = sh(
                                script: """
                                    curl -sS -o provision_status.json -w '%{http_code}' \
                                      '${params.PROVISION_API}/provision/${env.REQUEST_ID}/status'
                                """,
                                returnStdout: true
                            ).trim()

                            def statusText = readFile('provision_status.json').trim()
                            if (!(statusHttpCode in ['200'])) {
                                error "Provisioning status API returned HTTP ${statusHttpCode}: ${statusText}"
                            }

                            def currentStatus = sh(
                                script: '''
                                    sed -n 's/.*"status":"\\([^"]*\\)".*/\\1/p' provision_status.json
                                ''',
                                returnStdout: true
                            ).trim()
                            def currentMessage = sh(
                                script: '''
                                    sed -n 's/.*"message":"\\([^"]*\\)".*/\\1/p' provision_status.json
                                ''',
                                returnStdout: true
                            ).trim()

                            echo "Provisioning status: ${currentStatus} - ${currentMessage}"

                            if (currentStatus == 'READY') {
                                env.RESERVATION_ID = sh(
                                    script: '''
                                        sed -n 's/.*"reservation_id":"\\([^"]*\\)".*/\\1/p' provision_status.json
                                    ''',
                                    returnStdout: true
                                ).trim()
                                env.MACHINE_ID = sh(
                                    script: '''
                                        sed -n 's/.*"machine_id":"\\([^"]*\\)".*/\\1/p' provision_status.json
                                    ''',
                                    returnStdout: true
                                ).trim()
                                echo "Machine ready: ${env.MACHINE_ID}"
                                return true
                            }

                            if ([
                                'NO_ELIGIBLE_MACHINE',
                                'RESERVATION_FAILED',
                                'IMAGE_DEPLOY_FAILED',
                                'PROVISIONING_TIMEOUT',
                                'FAILED'
                            ].contains(currentStatus)) {
                                echo "Provision status response: ${statusText}"
                                error "Provisioning failed: ${currentStatus} - ${currentMessage}"
                            }

                            sleep 10
                            return false
                        }
                    }
                }
            }
        }

        stage('Run Validation') {
            steps {
                sh '''
                    echo "Running scenario: ${SELECTED_SCENARIO}"
                    echo "Reserved machine: ${MACHINE_ID}"
                    echo "Simulating validation execution on the provisioned machine..."
                    sleep 10
                '''
            }
        }

        stage('Publish Results') {
            steps {
                sh '''
                    echo "Collecting logs, test reports, and machine metadata..."
                    echo "Simulating result publishing and CI summary generation..."
                    sleep 10
                '''
            }
        }
    }

    post {
        always {
            script {
                if (env.RESERVATION_ID?.trim()) {
                    sh """
                        curl -s -X POST '${params.PROVISION_API}/reservations/${env.RESERVATION_ID}/release' || true
                    """
                }
            }
        }
    }
}
